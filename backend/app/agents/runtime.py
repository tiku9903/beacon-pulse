"""Agent runtime: Triage (already done by the pipeline) -> Specialist agent with tools -> Guardrail ->
deliver or hand to a human.

    customer msg ─▶ triage (intent, sentiment, safety) ─▶ specialist agent ──tool calls──▶ Magento / CRM /
                                                              │   ▲                      stock / policy /
                                                              │   └──── results ─────────  approvals
                                                              ▼
                                         respond_to_customer ─▶ guardrail ─▶ send / queue for human
"""
from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import responses
from ..ai import engine
from ..ai.providers import ProviderError
from ..ai.taxonomy import TEAMS
from ..config import get_settings
from ..models import Message, Ticket, TicketStatus, utcnow
from ..outbound import log_event, send_reply
from .specialists import GLOBAL_RULES, SPECIALISTS, Specialist, pick
from .tools import TOOLS, AgentContext, add_internal_note, run_tool

log = logging.getLogger("beacon.agents")
MAX_STEPS = 8
MAX_TRANSFERS = 2
MAX_GUARDRAIL_RETRIES = 2


# --------------------------------------------------------------------------- guardrail
_PROMISE = re.compile(
    r"\b(refund|return|replacement|claim|credit)\w*\b(?:[^.?!]|\.(?=\d)){0,60}\b(has|have|is|was|been)\s+(been\s+)?"
    r"(approved|processed|issued|completed|confirmed)\b|\bwe(?:'ve| have)\s+(refunded|approved|credited)\b|"
    r"\byou(?:'ll| will) (?:get|receive) (?:a |your )?(?:full )?refund\b", re.I)
_WIRING = re.compile(r"\b(connect|attach|strip|join|swap)\w*\b[^.?!]{0,50}\b(wires?|active|neutral|earth|terminal)\b", re.I)
_ORDER = re.compile(r"\bBL\s?-?\d{9}\b", re.I)
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_CODE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,}\b")


def check_reply(ctx: AgentContext, reply: str) -> list[str]:
    """Deterministic QA on every AI reply before a customer sees it."""
    problems = []
    if not reply or len(reply) < 2:
        return ["the reply is empty"]
    if len(reply) > 2500:
        problems.append("the reply is too long; keep it under 200 words")
    customer_text = " ".join(m.body for m in ctx.ticket.messages if m.direction == "inbound").upper()
    norm_customer = re.sub(r"[^0-9A-Z]", "", customer_text)
    for m in _ORDER.finditer(reply):
        oid = re.sub(r"[\s-]", "", m.group(0)).upper()
        if oid not in ctx.facts and oid not in norm_customer:
            problems.append(f"order {oid} was not returned by a tool - don't mention orders you haven't looked up")
    for m in _MONEY.finditer(reply):
        val = round(float(m.group(1).replace(",", "")), 2)
        if val not in ctx.amounts and m.group(1) not in customer_text and \
                not any(m.group(1) in f for f in ctx.facts):
            problems.append(f"${m.group(1)} doesn't come from a tool result - only quote amounts the tools gave you")
    for m in _CODE.finditer(reply.upper()):
        if m.group(0) not in ctx.facts and m.group(0) not in norm_customer and not m.group(0).startswith("BP"):
            problems.append(f"{m.group(0)} doesn't come from a tool result")
    approved = any(a.status == "approved" for a in ctx.ticket.approvals)
    if _PROMISE.search(reply) and not approved:
        problems.append("you promised an approved/processed refund, return or replacement - it is only SUBMITTED "
                        "for review; say the team will confirm")
    if _WIRING.search(reply):
        problems.append("no wiring instructions - say a licensed electrician must do hardwired work")
    return problems


# --------------------------------------------------------------------------- prompt + memory
def _history(db: Session, t: Ticket) -> list[dict]:
    """The conversation so far. In chat, earlier questions in the same session are remembered too."""
    if t.chat_session_id:
        ids = db.scalars(select(Ticket.id).where(Ticket.chat_session_id == t.chat_session_id)).all()
        msgs = db.scalars(select(Message).where(Message.ticket_id.in_(ids), Message.direction != "internal",
                                                Message.channel == "chat").order_by(Message.id)).all()
    else:
        msgs = [m for m in t.messages if m.direction != "internal"
                and not (m.author_type == "system" and "we've received your message" in m.body)]
    out: list[dict] = []
    for m in msgs[-20:]:
        role = "user" if m.direction == "inbound" else "assistant"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + m.body
        else:
            out.append({"role": role, "content": m.body})
    if not out or out[-1]["role"] != "user":
        out.append({"role": "user", "content": t.body})
    return out


def _system(sp: Specialist, t: Ticket, c: dict, customer) -> str:
    chat_note = (" (live chat: identity is self-declared, so tracking numbers are emailed, never shown here; "
                 "the customer must type their order number)") if t.channel == "chat" else ""
    who = f"{customer.name} ({customer.segment}{', ' + customer.trade_tier if customer.trade_tier else ''})" \
        if customer else f"{t.contact_name or 'unknown'} (not found in CRM)"
    return GLOBAL_RULES.format(
        title=sp.title, job=sp.job, channel=t.channel.replace("_", " "), chat_note=chat_note, ref=t.ref,
        customer=who, email=t.contact_email or "none", intent=c.get("intent"),
        confidence=f"{round(float(c.get('confidence') or 0) * 100)}%", sentiment=c.get("sentiment"),
        priority=t.priority, team=TEAMS.get(t.team or "", t.team), today=utcnow().strftime("%A %-d %B %Y"))


def _tool_schemas(sp: Specialist) -> list[dict]:
    return [TOOLS[n][1] for n in sp.tools]


# --------------------------------------------------------------------------- the loop
def _infer_outcome(ctx: AgentContext, text: str) -> str:
    if ctx.handoff:
        return "handed_off"
    if ctx.approvals:
        return "pending_review"
    return "needs_info" if text.rstrip().endswith("?") else "resolved"


def run_agents(db: Session, t: Ticket, c: dict, customer, allow_auto: bool = True) -> bool:
    """Returns True when the agents handled the ticket; False means 'use the rules engine instead'
    (no LLM configured, provider error, or the agent could not produce a safe reply)."""
    provider = engine.get_provider()
    if provider is None:
        return False
    started = time.perf_counter()
    sp = pick(c.get("intent"), c.get("confidence"), customer.segment if customer else None)
    ctx = AgentContext(db=db, ticket=t, agent_name=sp.title)
    messages = [{"role": "system", "content": _system(sp, t, c, customer)}, *_history(db, t)]
    log_event(db, t, "agent_start", sp.title, specialist=sp.key, provider=provider.name)
    transfers = guard_retries = steps = 0
    give_up = False

    try:
        while steps < MAX_STEPS and not ctx.final and not ctx.safety and not give_up:
            steps += 1
            turn = provider.chat(messages, _tool_schemas(sp))
            messages.append(turn.message)
            if not turn.tool_calls:
                if not turn.content:
                    messages.append({"role": "user", "content": "(system) Call respond_to_customer with your reply."})
                    continue
                ctx.final = {"message": turn.content, "outcome": _infer_outcome(ctx, turn.content)}
                problems = check_reply(ctx, ctx.final["message"])
                if problems and guard_retries < MAX_GUARDRAIL_RETRIES:
                    guard_retries += 1
                    log_event(db, t, "guardrail_blocked", sp.title, problems=problems)
                    ctx.final = None
                    messages.append({"role": "user", "content": "(system) Reply rejected: " + "; ".join(problems)
                                     + ". Fix it and call respond_to_customer."})
                    continue
                if problems:
                    ctx.final = None
                break
            for call in turn.tool_calls:
                if call.name not in sp.tools:
                    result = {"error": f"{call.name} is not available to the {sp.title}. Transfer to a specialist "
                                       "that has it, or hand off to a human."}
                else:
                    result = run_tool(ctx, call.name, call.arguments)
                    if call.name == "respond_to_customer" and ctx.final:
                        problems = check_reply(ctx, ctx.final["message"])
                        if problems:
                            log_event(db, t, "guardrail_blocked", sp.title, problems=problems)
                            ctx.final = None
                            if guard_retries >= MAX_GUARDRAIL_RETRIES:
                                give_up = True
                            guard_retries += 1
                            result = {"error": "Reply rejected by quality check: " + "; ".join(problems)
                                               + ". Fix it and call respond_to_customer again."}
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)})
            if ctx.transfer_to and transfers < MAX_TRANSFERS:
                transfers += 1
                new = SPECIALISTS[ctx.transfer_to]
                log_event(db, t, "agent_transfer", sp.title, to=new.title)
                sp, ctx.agent_name, ctx.transfer_to = new, new.title, None
                messages[0] = {"role": "system", "content": _system(sp, t, c, customer)}
    except ProviderError as e:
        log.warning("agent run failed for %s, falling back to rules: %s", t.ref, e)
        log_event(db, t, "agent_failed", sp.title, error=str(e)[:300])
        if not ctx.safety:
            return False

    if ctx.safety:
        _deliver_safety(db, t, ctx)
        return True
    if not ctx.final:
        log_event(db, t, "agent_failed", sp.title, error="no safe reply produced", steps=steps)
        return False
    _deliver(db, t, c, ctx, sp, allow_auto, steps, round((time.perf_counter() - started) * 1000), provider)
    return True


# --------------------------------------------------------------------------- outcomes
def _human_owned(t: Ticket) -> bool:
    return any(m.author_type == "agent" and m.direction == "outbound" for m in t.messages)


def _summary_note(ctx: AgentContext, sp: Specialist, outcome: str) -> str:
    lines = [f"AI agent summary ({sp.title}, outcome: {outcome.replace('_', ' ')})"]
    for step in ctx.trace:
        status = "failed: " + str(step["result"].get("error")) if isinstance(step["result"], dict) and \
            step["result"].get("error") else "ok"
        lines.append(f"- {step['tool']}({', '.join(f'{k}={v}' for k, v in (step['args'] or {}).items())}) -> {status}")
    if ctx.handoff:
        lines.append(f"Handoff to {TEAMS.get(ctx.handoff['team'], ctx.handoff['team'])}: {ctx.handoff.get('reason', '')}")
        if ctx.handoff.get("summary"):
            lines.append(ctx.handoff["summary"])
    return "\n".join(lines)


def _deliver(db, t, c, ctx: AgentContext, sp: Specialist, allow_auto, steps, ms, provider) -> None:
    from ..pipeline import _new_draft, _queue
    s = get_settings()
    reply, outcome = ctx.final["message"], ctx.final["outcome"]
    if ctx.handoff:
        outcome = "handed_off"
    elif ctx.approvals and outcome == "resolved":
        outcome = "pending_review"
    angry = c.get("sentiment") == "angry"
    author = f"Beacon AI ({sp.title})"
    deliver = s.automation_mode == "live" and allow_auto and not _human_owned(t)

    if ctx.handoff and ctx.handoff.get("team") in TEAMS and ctx.handoff["team"] != t.team:
        t.team, t.assigned_agent_id = ctx.handoff["team"], None
    if ctx.trace or ctx.handoff:
        add_internal_note(ctx, _summary_note(ctx, sp, outcome))

    if not deliver:
        _new_draft(db, t, reply)
        _queue(db, t, ack=False)
        log_event(db, t, "agent_draft", sp.title, outcome=outcome, steps=steps, ms=ms, model=provider.model)
        return

    send_reply(db, t, reply, "ai", author)
    if outcome == "pending_review" or any(a.status == "pending" for a in t.approvals):
        _queue(db, t, ack=False)  # -> awaiting_approval, assigned to the team that signs off
    elif outcome == "handed_off" or angry:
        _queue(db, t, ack=False)
        t.status = TicketStatus.AWAITING_AGENT
    elif outcome == "needs_info":
        t.status = TicketStatus.AWAITING_CUSTOMER
    elif t.intent in s.auto_resolve_intent_set and float(t.intent_confidence or 0) >= s.auto_resolve_min_confidence * 0.75:
        t.status, t.resolved_at, t.resolution_type = TicketStatus.AUTO_RESOLVED, utcnow(), "auto"
        log_event(db, t, "auto_resolved", sp.title, kind="agent")
    else:  # answered, but this path isn't cleared for full automation yet -> a human glances at it
        _queue(db, t, ack=False)
        t.status = TicketStatus.AWAITING_AGENT
    log_event(db, t, "ai_reply", sp.title, outcome=outcome, steps=steps, ms=ms, model=provider.model,
              approvals=ctx.approvals, handoff=(ctx.handoff or {}).get("team"))


def _deliver_safety(db, t: Ticket, ctx: AgentContext) -> None:
    """Safety replies are never AI-worded: fixed, legally reviewed advice + P1 human ownership."""
    from ..pipeline import _new_draft, _queue
    s = get_settings()
    if s.automation_mode == "live" and s.safety_auto_ack and not any(
            m.direction == "outbound" and "your safety comes first" in m.body for m in t.messages):
        send_reply(db, t, responses.safety_ack(t.contact_name, t.ref), "system", "Beacon Pulse")
    _new_draft(db, t, responses.draft_safety(t.contact_name, t.ref))
    add_internal_note(ctx, _summary_note(ctx, SPECIALISTS["general_agent"], "safety escalation"))
    _queue(db, t, ack=False)
    log_event(db, t, "ai_reply", ctx.agent_name, outcome="safety_escalation")
