"""Tools the agents can call. Every business rule lives HERE, in code, not in a prompt:
ownership checks before disclosing an order, the 30-day return window, warranty terms,
refund limits, approvals instead of money movement, and safety escalation.

Each tool returns a JSON-serialisable dict that is fed back to the model, and records the
facts it revealed (order ids, amounts, tracking numbers) so the guardrail can reject any
reply that mentions a fact no tool returned."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import notify, responses
from ..ai.taxonomy import INTENTS, TEAMS
from ..connectors import crm_connector, inventory_connector, order_connector
from ..connectors.base import Order
from ..knowledge import kb
from ..models import Message, Ticket, TicketStatus, utcnow
from ..outbound import log_event, send_reply

AI = "Beacon AI"
RETURN_WINDOW_DAYS = 30


@dataclass
class AgentContext:
    db: Session
    ticket: Ticket
    agent_name: str = ""
    facts: set[str] = field(default_factory=set)          # strings the reply may quote
    amounts: set[float] = field(default_factory=set)      # $ amounts the reply may quote
    approvals: list[str] = field(default_factory=list)     # approval types created this run
    handoff: dict | None = None
    safety: bool = False
    transfer_to: str | None = None
    emailed_orders: set[str] = field(default_factory=set)
    final: dict | None = None
    trace: list[dict] = field(default_factory=list)

    @property
    def is_chat(self) -> bool:
        return self.ticket.channel == "chat"

    def learn(self, *values) -> None:
        for v in values:
            if v is None:
                continue
            if isinstance(v, (int, float)):
                self.amounts.add(round(float(v), 2))
            else:
                self.facts.add(str(v).upper())


class ToolError(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def _norm_order(order_id: str) -> str:
    key = re.sub(r"[^0-9A-Za-z]", "", order_id or "").upper()
    return "BL" + key if key.isdigit() else key


def _verified(ctx: AgentContext, order: Order) -> bool:
    e = ctx.ticket.contact_email
    return bool(e and order.customer_email and order.customer_email.lower() == e.lower())


def _customer_gave(ctx: AgentContext, value: str) -> bool:
    text = re.sub(r"[^0-9A-Z]", "", " ".join(m.body for m in ctx.ticket.messages if m.direction == "inbound").upper())
    return re.sub(r"^BL", "", value.upper()) in text


def _order_view(ctx: AgentContext, o: Order) -> dict:
    """What the agent may see (and so say). On chat the identity is only self-declared, so the
    tracking number never goes on screen - it is emailed to the address on the order."""
    ctx.learn(o.order_id, o.total, o.carrier, o.pickup_store, *[l.unit_price for l in o.lines], *[l.name for l in o.lines])
    view = {
        "order_id": o.order_id, "status": o.status, "placed": o.placed_at.date().isoformat(),
        "items": [{"sku": l.sku, "name": l.name, "qty": l.qty, "unit_price": l.unit_price} for l in o.lines],
        "total": o.total, "fulfilment": o.fulfilment, "pickup_store": o.pickup_store, "carrier": o.carrier,
        "estimated_delivery": o.eta.strftime("%A %-d %B") if o.eta else None,
        "delivered_on": o.delivered_at.strftime("%A %-d %B") if o.delivered_at else None,
    }
    for d in (view["estimated_delivery"], view["delivered_on"], view["placed"]):
        ctx.learn(d)
    if ctx.is_chat:
        if o.tracking_number and o.order_id not in ctx.emailed_orders and ctx.ticket.contact_email:
            send_reply(ctx.db, ctx.ticket, responses.order_status(o, ctx.ticket.contact_name), "ai", AI,
                       channel="email", subject=f"Your order {o.order_id}", counts_as_response=False)
            ctx.emailed_orders.add(o.order_id)
        view["tracking"] = (f"tracking link emailed to {responses.mask_email(ctx.ticket.contact_email)}"
                            if o.tracking_number else None)
    else:
        view["tracking_number"], view["tracking_url"] = o.tracking_number, o.tracking_url
        ctx.learn(o.tracking_number, o.tracking_url)
    return view


def _get_verified_order(ctx: AgentContext, order_id: str) -> Order:
    o = order_connector().get_order(_norm_order(order_id))
    if not o:
        raise ToolError(f"No order {order_id} found. Ask the customer to check the number (starts with BL).")
    if not _verified(ctx, o):
        raise ToolError("That order is not linked to this customer's email address. Do not reveal anything about it; "
                        "ask them to contact us from the email they ordered with.")
    if ctx.is_chat and not _customer_gave(ctx, o.order_id):
        raise ToolError("In live chat the customer must type the order number themselves before we discuss it.")
    return o


def _request_approval(ctx: AgentContext, action: str, amount: float | None, payload: dict, rec: str) -> dict:
    from ..pipeline import _request_approval as create
    before = len(ctx.ticket.approvals)
    create(ctx.db, ctx.ticket, action, amount, payload, f"[{ctx.agent_name}] {rec}")
    ctx.approvals.append(action)
    created = len(ctx.ticket.approvals) > before
    return {"approval_request": action, "status": "pending human sign-off" if created else "already pending",
            "who_approves": "a supervisor" if action == "refund" else "the team",
            "tell_customer": "it has been submitted for review and the team will confirm; do NOT say it is approved"}


# --------------------------------------------------------------------------- tools
def get_customer_profile(ctx: AgentContext) -> dict:
    rec = crm_connector().find_customer(email=ctx.ticket.contact_email, phone=ctx.ticket.contact_phone)
    if not rec:
        return {"known_customer": False, "name_given": ctx.ticket.contact_name}
    ctx.learn(rec.name, rec.account_manager)
    return {"known_customer": True, "name": rec.name, "segment": rec.segment, "trade_tier": rec.trade_tier,
            "account_manager": rec.account_manager}


def find_orders(ctx: AgentContext, order_id: str | None = None) -> dict:
    if order_id:
        return {"order": _order_view(ctx, _get_verified_order(ctx, order_id))}
    if not ctx.ticket.contact_email:
        return {"orders": [], "note": "No email on file - ask for the order number."}
    orders = order_connector().find_orders_by_email(ctx.ticket.contact_email)
    if ctx.is_chat:  # never list orders on screen from a self-declared email
        return {"orders_on_file": len(orders), "note": "Ask the customer for the order number (starts with BL)."}
    return {"orders": [_order_view(ctx, o) for o in orders]}


def check_product_stock(ctx: AgentContext, sku: str | None = None, query: str | None = None) -> dict:
    inv = inventory_connector()
    items = [inv.get_stock(sku)] if sku else []
    items = [i for i in items if i] or inv.search_products(query or sku or "")
    out = []
    for i in items:
        ctx.learn(i.sku, i.name, i.price, *[s.store for s in i.stores])
        eta = i.restock_eta.strftime("%A %-d %B") if i.restock_eta else None
        ctx.learn(eta)
        out.append({"sku": i.sku, "name": i.name, "price": i.price, "online_in_stock": i.online_qty > 0,
                    "stores_with_stock": [{"store": s.store, "qty": s.qty} for s in i.stores if s.qty > 0],
                    "restock_expected": eta})
    return {"products": out} if out else {"products": [], "note": "No matching product. Ask for the product name or SKU."}


def search_policy(ctx: AgentContext, query: str) -> dict:
    hits = kb().search(query, k=3)
    for p, _ in hits:
        ctx.learn(*re.findall(r"\$?\d[\d,.]*", p.text))
    return {"passages": [{"title": p.title, "text": p.text} for p, _ in hits]} if hits else \
        {"passages": [], "note": "No policy found. Do not guess - hand off to a human."}


def check_return_eligibility(ctx: AgentContext, order_id: str) -> dict:
    o = _get_verified_order(ctx, order_id)
    ref = o.delivered_at or o.placed_at
    days = (utcnow() - ref).days
    ctx.learn(o.order_id, o.total, str(days), str(RETURN_WINDOW_DAYS))
    return {"order_id": o.order_id, "days_since_delivery": days, "window_days": RETURN_WINDOW_DAYS,
            "eligible_change_of_mind": days <= RETURN_WINDOW_DAYS,
            "items": [l.name for l in o.lines], "order_total": o.total}


def create_return_request(ctx: AgentContext, order_id: str, reason: str) -> dict:
    o = _get_verified_order(ctx, order_id)
    days = (utcnow() - (o.delivered_at or o.placed_at)).days
    if days > RETURN_WINDOW_DAYS:
        raise ToolError(f"Outside the {RETURN_WINDOW_DAYS}-day window (day {days}). Offer a warranty claim if faulty, "
                        "or hand off to the Returns team.")
    return _request_approval(ctx, "return", o.total, {"order_id": o.order_id, "reason": reason},
                             f"Within {RETURN_WINDOW_DAYS}-day window (day {days}). Reason: {reason}")


def create_refund_request(ctx: AgentContext, order_id: str, amount: float, reason: str) -> dict:
    o = _get_verified_order(ctx, order_id)
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ToolError("amount must be a number")
    if amount <= 0 or amount > o.total:
        raise ToolError(f"Refund amount must be between $0 and the order total ${o.total:,.2f}.")
    ctx.learn(amount)
    return _request_approval(ctx, "refund", amount, {"order_id": o.order_id, "reason": reason},
                             f"Customer reports: {reason}. Verify payment record. Order total ${o.total:,.2f}.")


def check_warranty(ctx: AgentContext, order_id: str, sku: str | None = None) -> dict:
    o = _get_verified_order(ctx, order_id)
    line = next((l for l in o.lines if sku and l.sku.upper() == sku.upper()), o.lines[0])
    months, term = responses.warranty_term(line.name)
    age = (utcnow() - o.placed_at).days
    ctx.learn(line.name, line.sku, term, str(age))
    return {"order_id": o.order_id, "product": line.name, "sku": line.sku, "warranty": term,
            "purchased_days_ago": age, "in_warranty": age <= months * 30.4,
            "evidence_needed": "photo or short video of the fault; electrician's certificate for hardwired items"}


def create_warranty_claim(ctx: AgentContext, order_id: str, fault_description: str, sku: str | None = None) -> dict:
    info = check_warranty(ctx, order_id, sku)
    if not info["in_warranty"]:
        ctx.handoff = {"team": "warranty", "reason": "Out-of-warranty claim needs a goodwill decision"}
        return {"claim": "outside warranty", "next": "handed to the Warranty team for review; tell the customer that"}
    o = order_connector().get_order(_norm_order(order_id))
    price = next((l.unit_price for l in o.lines if l.sku == info["sku"]), o.lines[0].unit_price)
    return _request_approval(ctx, "replacement", price,
                             {"order_id": info["order_id"], "sku": info["sku"], "fault": fault_description},
                             f"In warranty ({info['warranty']}, day {info['purchased_days_ago']}). Fault: {fault_description}")


def escalate_safety(ctx: AgentContext, description: str) -> dict:
    t = ctx.ticket
    ctx.safety = True
    t.safety_flag = True
    t.safety_reasons = sorted(set(t.safety_reasons or []) | {"agent_flagged"})
    t.priority, t.team, t.urgency = "P1", "safety", max(t.urgency or 0, 90)
    notify.alert_safety_team(t.ref, description, t.safety_reasons)
    return {"escalated": True, "next": "Safety team paged at P1. Fixed safety advice will be sent to the customer."}


def handoff_to_human(ctx: AgentContext, team: str, reason: str, summary: str) -> dict:
    if team not in TEAMS:
        raise ToolError(f"team must be one of {list(TEAMS)}")
    ctx.handoff = {"team": team, "reason": reason, "summary": summary}
    return {"handed_off": True, "team": TEAMS[team],
            "tell_customer": "a specialist from that team will reply; do not promise an outcome"}


def transfer_to_specialist(ctx: AgentContext, specialist: str, reason: str) -> dict:
    from .specialists import SPECIALISTS
    if specialist not in SPECIALISTS:
        raise ToolError(f"specialist must be one of {list(SPECIALISTS)}")
    ctx.transfer_to = specialist
    return {"transferred": specialist}


def respond_to_customer(ctx: AgentContext, message: str, outcome: str) -> dict:
    ctx.final = {"message": message.strip(), "outcome": outcome}
    return {"ok": True}


# --------------------------------------------------------------------------- schemas
def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props or {}, "required": required or []}}}


_S = {"type": "string"}
TOOLS = {
    "get_customer_profile": (get_customer_profile, _fn(
        "get_customer_profile", "Look up the customer in Customer 360: name, retail or trade, trade tier, account manager.")),
    "find_orders": (find_orders, _fn(
        "find_orders", "Get an order's live status from Magento by order number (BL + 9 digits). Without a number, "
        "lists the customer's recent orders (not available in live chat).", {"order_id": _S})),
    "check_product_stock": (check_product_stock, _fn(
        "check_product_stock", "Live stock and price for a product, online and per store, plus restock date.",
        {"sku": _S, "query": {"type": "string", "description": "product name words, e.g. 'rattan pendant'"}})),
    "search_policy": (search_policy, _fn(
        "search_policy", "Search Beacon's approved policies: returns, refunds, warranty, delivery, click and collect, "
        "store hours, price match, trade, technical troubleshooting, electrical safety.", {"query": _S}, ["query"])),
    "check_return_eligibility": (check_return_eligibility, _fn(
        "check_return_eligibility", "Check whether an order is inside the 30-day change-of-mind window.",
        {"order_id": _S}, ["order_id"])),
    "create_return_request": (create_return_request, _fn(
        "create_return_request", "Submit a return for team approval (only if eligible). Does not refund anything itself.",
        {"order_id": _S, "reason": _S}, ["order_id", "reason"])),
    "create_refund_request": (create_refund_request, _fn(
        "create_refund_request", "Submit a refund for SUPERVISOR approval, e.g. a double charge. Never approves it.",
        {"order_id": _S, "amount": {"type": "number"}, "reason": _S}, ["order_id", "amount", "reason"])),
    "check_warranty": (check_warranty, _fn(
        "check_warranty", "Warranty term and whether the product is still covered.",
        {"order_id": _S, "sku": _S}, ["order_id"])),
    "create_warranty_claim": (create_warranty_claim, _fn(
        "create_warranty_claim", "Submit a warranty replacement for team approval.",
        {"order_id": _S, "sku": _S, "fault_description": _S}, ["order_id", "fault_description"])),
    "escalate_safety": (escalate_safety, _fn(
        "escalate_safety", "Use IMMEDIATELY for any electrical hazard (sparks, smoke, burning smell, heat, melting, "
        "shock), injury, a fitting falling, or a recall question.", {"description": _S}, ["description"])),
    "handoff_to_human": (handoff_to_human, _fn(
        "handoff_to_human", "Pass the case to a human team when it needs judgement, the customer asks for a person, "
        "policy does not cover it, or the customer is very upset.",
        {"team": {"type": "string", "enum": list(TEAMS)}, "reason": _S,
         "summary": {"type": "string", "description": "2-3 lines for the human: what happened, what you checked"}},
        ["team", "reason", "summary"])),
    "transfer_to_specialist": (transfer_to_specialist, _fn(
        "transfer_to_specialist", "Hand the conversation to a better-suited AI specialist when triage picked the wrong one.",
        {"specialist": _S, "reason": _S}, ["specialist", "reason"])),
    "respond_to_customer": (respond_to_customer, _fn(
        "respond_to_customer", "Send your final reply to the customer. ALWAYS finish by calling this exactly once.",
        {"message": _S, "outcome": {"type": "string", "enum": ["resolved", "needs_info", "pending_review", "handed_off"],
                                    "description": "resolved = fully answered; needs_info = you asked the customer a "
                                                   "question; pending_review = an approval request was created; "
                                                   "handed_off = a human team takes over"}},
        ["message", "outcome"])),
}


def run_tool(ctx: AgentContext, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    fn, _ = TOOLS[name]
    try:
        allowed = fn.__code__.co_varnames[1:fn.__code__.co_argcount]
        result = fn(ctx, **{k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")})
    except ToolError as e:
        result = {"error": str(e)}
    except TypeError as e:
        result = {"error": f"bad arguments: {e}"}
    if name != "respond_to_customer":
        ctx.trace.append({"agent": ctx.agent_name, "tool": name, "args": args,
                          "result": result if len(str(result)) < 600 else {"summary": str(result)[:600]}})
        log_event(ctx.db, ctx.ticket, "agent_tool", ctx.agent_name, tool=name, args=args,
                  ok="error" not in result, error=result.get("error"))
    return result


def add_internal_note(ctx: AgentContext, body: str) -> None:
    ctx.ticket.messages.append(Message(direction="internal", author_type="ai", author_name=AI, body=body))


__all__ = ["AgentContext", "TOOLS", "run_tool", "add_internal_note", "INTENTS", "TicketStatus", "timedelta"]
