"""The 5-phase workflow: Intake -> Identify -> Classify -> Route -> Resolve."""
import logging
import re
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notify, responses, routing
from .ai import engine
from .ai.taxonomy import TEAMS
from .config import get_settings
from .connectors import crm_connector, inventory_connector, order_connector
from .connectors.base import Order
from .knowledge import kb
from .models import Approval, AuditEvent, Customer, Draft, Message, Ticket, TicketStatus, utcnow
from .outbound import log_event, send_reply

log = logging.getLogger("beacon.pipeline")
AI = "Beacon Pulse AI"
KB_AUTO_MIN_SCORE = 3.0


# ---------------------------------------------------------------- 1. INTAKE
def _same_sender(t: Ticket, email: str | None, phone: str | None) -> bool:
    """Only the original contact may continue a thread - a guessed [BP-xxxxxx] ref or a forged
    In-Reply-To from someone else starts a new ticket instead."""
    if email and t.contact_email and email.strip().lower() == t.contact_email.lower():
        return True
    digits = lambda p: re.sub(r"\D", "", p or "")[-9:]  # noqa: E731
    return bool(phone and t.contact_phone and digits(phone) == digits(t.contact_phone))


def intake(db: Session, *, channel: str, body: str, subject: str | None = None, email: str | None = None,
           name: str | None = None, phone: str | None = None, chat_session_id: str | None = None,
           external_thread_id: str | None = None) -> tuple[Ticket, bool]:
    """Creates a ticket, or appends to the open thread it belongs to. Returns (ticket, is_new)."""
    existing = None
    if chat_session_id:
        existing = db.scalar(select(Ticket).where(Ticket.chat_session_id == chat_session_id)
                             .order_by(Ticket.id.desc()))
    else:
        if external_thread_id:
            existing = db.scalar(select(Ticket).where(Ticket.external_thread_id == external_thread_id)
                                 .order_by(Ticket.id.desc()))
        if not existing and subject:
            m = re.search(r"\[(BP-\d{6})\]", subject)
            if m:
                existing = db.scalar(select(Ticket).where(Ticket.ref == m.group(1)))
        if existing and not _same_sender(existing, email, phone):
            existing = None
    # in chat, a message after a resolved answer is a new question -> new ticket in the same session
    new_chat_question = channel == "chat" and existing is not None and existing.status in (
        TicketStatus.AUTO_RESOLVED, TicketStatus.RESOLVED)
    if existing and existing.status != TicketStatus.CLOSED and not new_chat_question:
        existing.messages.append(Message(direction="inbound", author_type="customer",
                       author_name=name or existing.contact_name, body=body, channel=channel, delivered=True))
        log_event(db, existing, "customer_follow_up", name or "customer", previous_status=existing.status)
        if existing.status in (TicketStatus.AUTO_RESOLVED, TicketStatus.RESOLVED, TicketStatus.AWAITING_CUSTOMER):
            existing.status = TicketStatus.NEW
            existing.resolved_at = None
            existing.resolution_type = None  # whoever resolves it now gets the credit (keeps automation % honest)
        db.commit()
        return existing, False

    t = Ticket(ref=f"TMP-{uuid.uuid4().hex[:12]}", channel=channel, subject=subject, body=body, contact_email=(email or None),
               contact_name=name, contact_phone=phone, chat_session_id=chat_session_id,
               external_thread_id=external_thread_id, status=TicketStatus.NEW)
    db.add(t)
    db.flush()
    t.ref = f"BP-{t.id:06d}"  # derived from the primary key: no race between concurrent intakes
    t.messages.append(Message(direction="inbound", author_type="customer", author_name=name or email,
                   body=body, channel=channel, delivered=True))
    log_event(db, t, "intake", channel=channel)
    db.commit()
    return t, True


# ---------------------------------------------------------------- 2. IDENTIFY
def identify(db: Session, t: Ticket) -> Customer | None:
    rec = crm_connector().find_customer(email=t.contact_email, phone=t.contact_phone)
    if not rec:
        log_event(db, t, "identify", result="unknown_customer")
        return None
    c = db.scalar(select(Customer).where(Customer.crm_id == rec.crm_id))
    if not c:
        c = Customer(crm_id=rec.crm_id)
        db.add(c)
    c.name, c.email, c.phone = rec.name, rec.email, rec.phone
    c.segment, c.trade_tier, c.lifetime_value = rec.segment, rec.trade_tier, rec.lifetime_value
    db.flush()
    t.customer_id = c.id
    t.customer = c
    if not t.contact_name:
        t.contact_name = rec.name
    log_event(db, t, "identify", result="matched", crm_id=rec.crm_id, segment=rec.segment, tier=rec.trade_tier)
    return c


def _customer_ctx(t: Ticket) -> dict:
    c = t.customer
    rec = crm_connector().find_customer(email=t.contact_email, phone=t.contact_phone) if c else None
    if not c:
        return {"name": t.contact_name, "segment": "retail", "known_customer": False}
    return {"name": c.name, "segment": c.segment, "trade_tier": c.trade_tier, "known_customer": True,
            "account_manager": getattr(rec, "account_manager", None)}


def _find_order(t: Ticket, ents: dict) -> tuple[Order | None, bool]:
    """Returns (order, verified). An order is only 'verified' when it belongs to the contact's
    email - we never disclose order details to someone who just typed a number."""
    oc = order_connector()
    order = None
    if ents.get("order_id"):
        order = oc.get_order(ents["order_id"])
    elif t.contact_email:
        orders = oc.find_orders_by_email(t.contact_email)
        open_orders = [o for o in orders if o.status in ("processing", "shipped", "ready_for_pickup")]
        order = (open_orders or orders or [None])[0]
    verified = bool(order and t.contact_email and order.customer_email
                    and order.customer_email.lower() == t.contact_email.lower())
    return order, verified


# ---------------------------------------------------------------- 5. RESOLVE helpers
def _thread_text(t: Ticket) -> str:
    inbound = [m.body for m in t.messages if m.direction == "inbound"]
    return "\n\n".join(inbound) if inbound else t.body


def _already_auto_answered(db: Session, t: Ticket) -> bool:
    return bool(db.scalar(select(func.count()).select_from(AuditEvent)
                          .where(AuditEvent.ticket_id == t.id, AuditEvent.event == "auto_resolved")))


def _new_draft(db: Session, t: Ticket, body: str, sources: list | None = None) -> None:
    for d in t.drafts:
        if d.status == "pending":
            d.status = "superseded"
    t.drafts.append(Draft(body=body, status="pending", kb_sources=sources or []))


def _request_approval(db: Session, t: Ticket, action: str, amount: float | None, payload: dict, rec: str) -> None:
    if any(a.status == "pending" and a.action_type == action for a in t.approvals):
        return
    a = Approval(action_type=action, status="pending", amount=amount, payload=payload, ai_recommendation=rec)
    t.approvals.append(a)
    log_event(db, t, "approval_requested", AI, action=action, amount=amount)


def _auto_send(db: Session, t: Ticket, body: str, kind: str, resolves: bool = True,
               email_copy: tuple[str, str] | None = None) -> None:
    """Sends an automated reply - or, in shadow mode, parks it as a draft for the parallel run."""
    s = get_settings()
    if s.automation_mode == "shadow":
        _new_draft(db, t, body)
        t.status = TicketStatus.AWAITING_AGENT
        log_event(db, t, "shadow_auto_resolution", AI, kind=kind)
        return
    if email_copy and t.contact_email:
        subject, private_body = email_copy
        send_reply(db, t, private_body, "ai", AI, channel="email", subject=subject)
    send_reply(db, t, body, "ai", AI)
    if resolves:
        t.status, t.resolved_at, t.resolution_type = TicketStatus.AUTO_RESOLVED, utcnow(), "auto"
        log_event(db, t, "auto_resolved", AI, kind=kind)
    else:
        t.status = TicketStatus.AWAITING_CUSTOMER
        log_event(db, t, "auto_clarification", AI, kind=kind)


def _queue(db: Session, t: Ticket, ack: bool = True) -> None:
    t.status = TicketStatus.AWAITING_APPROVAL if any(a.status == "pending" for a in t.approvals) else TicketStatus.AWAITING_AGENT
    if not t.assigned_agent_id:
        agent = routing.pick_agent(db, t.team)
        if agent:
            t.assigned_agent_id = agent.id
            log_event(db, t, "assigned", AI, agent=agent.name, team=t.team)
    if ack and get_settings().automation_mode == "live" and not any(m.direction == "outbound" for m in t.messages):
        hours = routing.SLA_HOURS[t.priority]
        send_reply(db, t, responses.acknowledgement(t.contact_name, t.ref, TEAMS[t.team], hours),
                   "system", "Beacon Pulse", counts_as_response=False)


# ---------------------------------------------------------------- the workflow
def process(db: Session, t: Ticket, forced_intent: str | None = None, allow_auto: bool = True) -> Ticket:
    """Runs phases 2-5. `forced_intent` = an agent's correction; `allow_auto=False` keeps a human in charge."""
    s = get_settings()
    timings = {}
    t0 = time.perf_counter()
    db.refresh(t)

    # 2. Identify
    customer = identify(db, t) if not t.customer_id else t.customer
    timings["identify_ms"] = round((time.perf_counter() - t0) * 1000)

    # 3. Classify
    t1 = time.perf_counter()
    text = _thread_text(t)
    ctx_customer = _customer_ctx(t)
    c, provider = engine.classify(text, ctx_customer)
    if forced_intent:
        c["intent"], c["confidence"] = forced_intent, 1.0
    # Safety is sticky: once raised (by rules, the LLM or an agent) only an explicit agent action clears it.
    if t.safety_flag:
        c["safety_concern"] = True
        c["safety_reasons"] = sorted(set(t.safety_reasons or []) | set(c.get("safety_reasons") or []))
        c["urgency"] = max(c.get("urgency", 0), 85)
    t.intent, t.intent_confidence, t.entities = c["intent"], c["confidence"], c["entities"]
    t.sentiment, t.urgency = c["sentiment"], c["urgency"]
    t.safety_flag, t.safety_reasons = c["safety_concern"], c.get("safety_reasons", [])
    t.classification_reasoning = (c.get("summary", "") + " | " + c.get("reasoning", "")).strip(" |")
    t.ai_provider = provider
    timings["classify_ms"] = round((time.perf_counter() - t1) * 1000)
    log_event(db, t, "classified", AI, intent=t.intent, confidence=t.intent_confidence, sentiment=t.sentiment,
              urgency=t.urgency, safety=t.safety_flag, provider=provider)

    # 4. Route
    why = routing.apply(db, t, c)
    log_event(db, t, "routed", AI, priority=t.priority, team=t.team, reasons=why)

    # 5. Resolve: AI agents when an LLM is configured, otherwise (or on any agent failure) the rules engine
    t2 = time.perf_counter()
    handled = False
    if s.agent_mode and not t.safety_flag:
        from .agents.runtime import run_agents
        handled = run_agents(db, t, c, customer, allow_auto)
    if not handled:
        _resolve_with_rules(db, t, c, customer, text, ctx_customer, allow_auto)

    timings["resolve_ms"] = round((time.perf_counter() - t2) * 1000)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
    log_event(db, t, "pipeline_complete", AI, status=t.status, **timings)
    db.commit()
    _auto_approvals(db, t)
    db.refresh(t)
    return t


def _resolve_with_rules(db: Session, t: Ticket, c: dict, customer, text: str, ctx_customer: dict,
                        allow_auto: bool) -> None:
    """Deterministic resolution: grounded templates, LLM (if any) only for agent drafts."""
    s = get_settings()
    name = (customer.name if customer else None) or t.contact_name
    ents = t.entities or {}
    intent = t.intent
    follow_up_after_auto = _already_auto_answered(db, t)
    can_auto = (intent in s.auto_resolve_intent_set and t.intent_confidence >= s.auto_resolve_min_confidence
                and not t.safety_flag and t.sentiment != "angry" and not follow_up_after_auto and allow_auto)
    if follow_up_after_auto:
        log_event(db, t, "human_handoff", AI, reason="customer replied after an automated answer")

    context: dict = {"customer": ctx_customer, "ticket_ref": t.ref}
    order, verified = (None, False)
    if intent in ("order_status", "return_request", "refund_billing", "warranty_claim") or ents.get("order_id"):
        order, verified = _find_order(t, ents)
        if order and verified and t.channel == "chat":
            # Chat identity is self-declared: order details only ever go to the order's email inbox.
            context["order_note"] = ("Live chat identity is not verified. Do not include order details in the reply; "
                                     "say they have been sent to the email address on the order.")
        elif order and verified:
            context["order"] = order.to_dict()
        elif order:
            context["order_note"] = "An order number was given but it does not belong to this contact's email - do not disclose its details; ask the customer to confirm from the purchasing email."
            order = None

    if t.safety_flag:
        # Safety first: immediate fixed-wording safety advice, page the safety team, human owns it.
        if s.safety_auto_ack and not any(m.direction == "outbound" for m in t.messages) and s.automation_mode == "live":
            send_reply(db, t, responses.safety_ack(name, t.ref), "system", "Beacon Pulse")
        notify.alert_safety_team(t.ref, c.get("summary", ""), t.safety_reasons)
        passages = kb().search("safety " + text, k=2)
        context["policy"] = [p.text for p, _ in passages]
        body, _ = engine.draft(text, c, context, responses.draft_safety(name, t.ref))
        _new_draft(db, t, body, [p.as_source() for p, _ in passages])
        _queue(db, t, ack=False)

    elif intent == "order_status":
        if order and can_auto and t.channel == "chat":
            _auto_send(db, t, responses.order_status_by_email(name, t.contact_email), "order_status",
                       email_copy=(f"Your order {order.order_id}", responses.order_status(order, name)))
        elif order and can_auto:
            _auto_send(db, t, responses.order_status(order, name), "order_status")
        elif not order and not ents.get("order_id") and can_auto:
            _auto_send(db, t, responses.ask_order_number(name), "ask_order_number", resolves=False)
        else:
            fb = (responses.order_status_by_email(name, t.contact_email) if order and t.channel == "chat"
                  else responses.order_status(order, name) if order else responses.ask_order_number(name))
            body, _ = engine.draft(text, c, context, fb)
            _new_draft(db, t, body)
            _queue(db, t)

    elif intent == "product_availability":
        inv = inventory_connector()
        stocks = []
        if ents.get("sku"):
            st = inv.get_stock(ents["sku"])
            stocks = [st] if st else []
        if not stocks:
            stocks = inv.search_products(ents.get("product_query") or text)
        context["stock"] = [st.to_dict() for st in stocks]
        if stocks and can_auto:
            _auto_send(db, t, responses.availability(stocks, name), "product_availability")
        else:
            fb = responses.availability(stocks, name) if stocks else responses.draft_generic(name)
            body, _ = engine.draft(text, c, context, fb)
            _new_draft(db, t, body)
            _queue(db, t)

    elif intent in ("store_policy", "technical_support", "other"):
        passages = kb().search(text, k=3)
        sources = [p.as_source() for p, _ in passages]
        context["policy"] = [{"title": p.title, "text": p.text} for p, _ in passages]
        top = passages[0][1] if passages else 0
        if passages and top >= KB_AUTO_MIN_SCORE and can_auto:
            _auto_send(db, t, responses.policy([p for p, _ in passages], name), intent)
            log_event(db, t, "kb_answer", AI, sources=sources, top_score=top)
        else:
            body, _ = engine.draft(text, c, context, responses.draft_from_kb(name, [p for p, _ in passages]))
            _new_draft(db, t, body, sources)
            _queue(db, t)

    elif intent == "return_request":
        eligible = None
        if order:
            ref_date = order.delivered_at or order.placed_at
            eligible = (utcnow() - ref_date) <= timedelta(days=30)
            context["return_window"] = {"days_since_delivery": (utcnow() - ref_date).days, "eligible": eligible}
            if eligible:
                _request_approval(db, t, "return", order.total, {"order_id": order.order_id},
                                  f"Within 30-day window (day {(utcnow() - ref_date).days} of 30). Recommend return label + refund on receipt.")
        passages = kb().search("return " + text, k=2)
        context["policy"] = [p.text for p, _ in passages]
        body, _ = engine.draft(text, c, context, responses.draft_return(name, order, eligible))
        _new_draft(db, t, body, [p.as_source() for p, _ in passages])
        _queue(db, t)

    elif intent == "refund_billing":
        if order:  # a refund can only be executed against a verified order; otherwise an agent investigates
            amount = ents.get("amount") or order.total
            _request_approval(db, t, "refund", amount, {"order_id": order.order_id},
                              f"Verify payment record before approving. Order total ${order.total:,.2f}.")
        body, _ = engine.draft(text, c, context, responses.draft_refund(name, order))
        _new_draft(db, t, body)
        _queue(db, t)

    elif intent == "warranty_claim":
        in_warranty, term = None, None
        if order:
            months, term = responses.warranty_term(order.lines[0].name)
            age_days = (utcnow() - order.placed_at).days
            in_warranty = age_days <= months * 30.4
            context["warranty"] = {"term": term, "purchase_age_days": age_days, "in_warranty": in_warranty}
            if in_warranty:
                _request_approval(db, t, "replacement", order.lines[0].unit_price,
                                  {"order_id": order.order_id, "sku": order.lines[0].sku},
                                  f"In warranty ({term}, purchased {age_days} days ago). Recommend replacement once photos received.")
        passages = kb().search("warranty claim " + text, k=2)
        context["policy"] = [p.text for p, _ in passages]
        body, _ = engine.draft(text, c, context, responses.draft_warranty(name, order, in_warranty, term))
        _new_draft(db, t, body, [p.as_source() for p, _ in passages])
        _queue(db, t)

    elif intent == "trade_inquiry":
        rec = crm_connector().find_customer(email=t.contact_email, phone=t.contact_phone)
        passages = kb().search("trade " + text, k=2)
        context["policy"] = [p.text for p, _ in passages]
        body, _ = engine.draft(text, c, context, responses.draft_trade(name, rec))
        _new_draft(db, t, body, [p.as_source() for p, _ in passages])
        _queue(db, t)

    else:
        body, _ = engine.draft(text, c, context, responses.draft_generic(name))
        _new_draft(db, t, body)
        _queue(db, t)


def _auto_approvals(db: Session, t: Ticket) -> None:
    """Refunds under REFUND_AUTO_APPROVE_LIMIT go through the same execute-and-notify path as a human decision."""
    limit = get_settings().refund_auto_approve_limit
    if limit <= 0 or t.safety_flag:
        return
    from . import approvals
    for a in list(t.approvals):
        if a.status == "pending" and a.action_type == "refund" and a.amount is not None and a.amount <= limit:
            try:
                approvals.decide(db, a, "approve", None, note=f"auto-approved under ${limit:,.2f} limit")
            except Exception:
                log.exception("auto-approval failed for approval %s - left for a human", a.id)


def safe_process(db: Session, t: Ticket, **kw) -> Ticket:
    """process() with the never-lose-a-ticket fallback. Every channel goes through this."""
    tid = t.id
    try:
        return process(db, t, **kw)
    except Exception:
        log.exception("pipeline failed for ticket %s", tid)
        db.rollback()
        t = db.get(Ticket, tid)
        # never lose a ticket: drop it into the general queue for a human
        t.status, t.team, t.priority = TicketStatus.AWAITING_AGENT, t.team or "customer_support", t.priority or "P2"
        if not t.sla_due_at:
            t.sla_due_at = t.created_at + timedelta(hours=routing.SLA_HOURS[t.priority])
        log_event(db, t, "pipeline_error", "system")
        db.commit()
        return t


def process_by_id(ticket_id: int) -> None:
    """Background-task entry point (own session)."""
    from .db import SessionLocal
    db = SessionLocal()
    try:
        t = db.get(Ticket, ticket_id)
        if t:
            safe_process(db, t)
    finally:
        db.close()
