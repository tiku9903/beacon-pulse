"""Agent console: queues, ticket workspace, AI drafts, approvals and safety alerts."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import actions, pipeline, routing
from .. import approvals as approval_service
from ..ai.taxonomy import INTENTS, TEAMS
from ..auth import create_token, current_agent, hash_password, require_role, verify_password
from ..db import get_db
from ..models import Agent, Approval, Message, Ticket, TicketStatus, utcnow
from ..outbound import log_event, send_reply
from ..serializers import approval_dict, ticket_detail, ticket_summary

router = APIRouter(prefix="/api", tags=["console"])


# ------------------------------------------------------------------ auth
class Login(BaseModel):
    email: str
    password: str


def _agent_dict(a: Agent) -> dict:
    return {"id": a.id, "name": a.name, "email": a.email, "role": a.role, "skills": a.skills,
            "max_active_tickets": a.max_active_tickets, "active": a.active}


@router.post("/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    a = db.scalar(select(Agent).where(Agent.email == body.email.lower().strip()))
    if not a or not a.active or not verify_password(body.password, a.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(a), "agent": _agent_dict(a)}


@router.get("/auth/me")
def me(agent: Agent = Depends(current_agent)):
    return _agent_dict(agent)


@router.get("/meta")
def meta():
    return {"intents": INTENTS, "teams": TEAMS, "sla_hours": routing.SLA_HOURS,
            "statuses": ["new", "auto_resolved", "awaiting_agent", "awaiting_approval", "awaiting_customer",
                         "resolved", "closed"]}


# ------------------------------------------------------------------ agents
class AgentIn(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)
    role: str = Field(default="agent", pattern="^(agent|supervisor|admin)$")
    skills: list[str] = []
    max_active_tickets: int = 25


@router.get("/agents")
def list_agents(db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    load = dict(db.execute(select(Ticket.assigned_agent_id, func.count()).where(
        Ticket.status.in_(TicketStatus.OPEN)).group_by(Ticket.assigned_agent_id)).all())
    return [_agent_dict(a) | {"open_tickets": load.get(a.id, 0)} for a in db.scalars(select(Agent).order_by(Agent.name))]


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, db: Session = Depends(get_db), _: Agent = Depends(require_role("admin"))):
    if db.scalar(select(Agent).where(Agent.email == body.email.lower())):
        raise HTTPException(409, "email already exists")
    bad = set(body.skills) - set(TEAMS)
    if bad:
        raise HTTPException(422, f"unknown skills: {bad}")
    a = Agent(name=body.name, email=body.email.lower(), password_hash=hash_password(body.password), role=body.role,
              skills=body.skills, max_active_tickets=body.max_active_tickets)
    db.add(a)
    db.commit()
    return _agent_dict(a)


# ------------------------------------------------------------------ tickets
@router.get("/tickets")
def list_tickets(status: str | None = None, team: str | None = None, priority: str | None = None,
                 intent: str | None = None, mine: bool = False, safety: bool | None = None, q: str | None = None,
                 open_only: bool = False, limit: int = 100, offset: int = 0,
                 db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    stmt = select(Ticket)
    if status:
        stmt = stmt.where(Ticket.status.in_(status.split(",")))
    if open_only:
        stmt = stmt.where(Ticket.status.in_(TicketStatus.OPEN))
    if team:
        stmt = stmt.where(Ticket.team == team)
    if priority:
        stmt = stmt.where(Ticket.priority.in_(priority.split(",")))
    if intent:
        stmt = stmt.where(Ticket.intent == intent)
    if mine:
        stmt = stmt.where(Ticket.assigned_agent_id == agent.id)
    if safety is not None:
        stmt = stmt.where(Ticket.safety_flag.is_(safety))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Ticket.ref.ilike(like), Ticket.subject.ilike(like), Ticket.body.ilike(like),
                              Ticket.contact_email.ilike(like), Ticket.contact_name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Ticket.priority, Ticket.sla_due_at.is_(None), Ticket.sla_due_at,
                                    Ticket.created_at.desc()).limit(min(limit, 500)).offset(offset)).all()
    return {"total": total, "items": [ticket_summary(t) for t in rows]}


def _ticket(db: Session, ticket_id: int) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if not t:
        raise HTTPException(404, "ticket not found")
    return t


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    return ticket_detail(_ticket(db, ticket_id))


class Reply(BaseModel):
    body: str = Field(min_length=1)
    draft_id: int | None = None
    resolve: bool = True


@router.post("/tickets/{ticket_id}/reply")
def reply(ticket_id: int, body: Reply, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    t = _ticket(db, ticket_id)
    used_draft = None
    if body.draft_id:
        used_draft = next((d for d in t.drafts if d.id == body.draft_id), None)
        if used_draft:
            used_draft.status = "sent"
            used_draft.was_edited = used_draft.body.strip() != body.body.strip()
    for d in t.drafts:
        if d.status == "pending":
            d.status = "superseded"
    send_reply(db, t, body.body, "agent", agent.name)
    if not t.assigned_agent_id:
        t.assigned_agent_id = agent.id
    t.resolution_type = "ai_assisted" if used_draft else (t.resolution_type if t.resolution_type == "ai_assisted" else "manual")
    if any(a.status == "pending" for a in t.approvals):
        t.status = TicketStatus.AWAITING_APPROVAL
    elif body.resolve:
        t.status, t.resolved_at = TicketStatus.RESOLVED, utcnow()
    else:
        t.status = TicketStatus.AWAITING_CUSTOMER
    log_event(db, t, "agent_reply", agent.name, used_draft=bool(used_draft),
              draft_edited=used_draft.was_edited if used_draft else None)
    db.commit()
    return ticket_detail(t)


class Note(BaseModel):
    body: str = Field(min_length=1)


@router.post("/tickets/{ticket_id}/note")
def add_note(ticket_id: int, body: Note, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    t = _ticket(db, ticket_id)
    t.messages.append(Message(direction="internal", author_type="agent", author_name=agent.name, body=body.body))
    db.commit()
    return ticket_detail(t)


class Assign(BaseModel):
    agent_id: int | None = None
    team: str | None = None


@router.post("/tickets/{ticket_id}/assign")
def assign(ticket_id: int, body: Assign, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    t = _ticket(db, ticket_id)
    if body.team:
        if body.team not in TEAMS:
            raise HTTPException(422, "unknown team")
        t.team = body.team
    if body.agent_id is not None and not db.get(Agent, body.agent_id):
        raise HTTPException(404, "agent not found")
    t.assigned_agent_id = body.agent_id
    log_event(db, t, "reassigned", agent.name, agent_id=body.agent_id, team=t.team)
    db.commit()
    return ticket_detail(t)


class StatusIn(BaseModel):
    status: str


@router.post("/tickets/{ticket_id}/status")
def set_status(ticket_id: int, body: StatusIn, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    allowed = {"awaiting_agent", "awaiting_customer", "resolved", "closed"}
    if body.status not in allowed:
        raise HTTPException(422, f"status must be one of {allowed}")
    t = _ticket(db, ticket_id)
    t.status = body.status
    if body.status in ("resolved", "closed") and not t.resolved_at:
        t.resolved_at = utcnow()
        t.resolution_type = t.resolution_type or "manual"
    log_event(db, t, "status_changed", agent.name, status=body.status)
    db.commit()
    return ticket_detail(t)


class Reclassify(BaseModel):
    intent: str


@router.post("/tickets/{ticket_id}/reclassify")
def reclassify(ticket_id: int, body: Reclassify, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    """Agent correction. Recorded as a labelled example (accuracy metric + future fine-tuning data)
    and the ticket is re-routed and re-drafted under the corrected intent."""
    if body.intent not in INTENTS:
        raise HTTPException(422, "unknown intent")
    t = _ticket(db, ticket_id)
    log_event(db, t, "reclassified", agent.name, from_intent=t.intent, to_intent=body.intent)
    t.agent_corrected_intent = body.intent
    t.assigned_agent_id = None  # re-routed to the right team for the corrected intent
    db.commit()
    # full resolve phase under the corrected intent (approvals, grounded draft); a human stays in charge
    pipeline.safe_process(db, t, forced_intent=body.intent, allow_auto=False)
    db.refresh(t)
    return ticket_detail(t)


class SafetyIn(BaseModel):
    safety_flag: bool
    note: str = Field(min_length=3)


@router.post("/tickets/{ticket_id}/safety")
def set_safety(ticket_id: int, body: SafetyIn, db: Session = Depends(get_db),
               agent: Agent = Depends(require_role("supervisor", "admin"))):
    """Safety flags are sticky; only a supervisor can clear a false positive (with a reason)."""
    t = _ticket(db, ticket_id)
    t.safety_flag = body.safety_flag
    if not body.safety_flag:
        t.safety_reasons = []
    log_event(db, t, "safety_flag_changed", agent.name, safety_flag=body.safety_flag, note=body.note)
    db.commit()
    return ticket_detail(t)


@router.post("/tickets/{ticket_id}/redraft")
def redraft(ticket_id: int, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    t = _ticket(db, ticket_id)
    log_event(db, t, "redrafted", agent.name)
    db.commit()
    # re-runs the resolve phase for the current intent with fresh order/stock/policy context
    pipeline.safe_process(db, t, forced_intent=t.agent_corrected_intent or t.intent, allow_auto=False)
    db.refresh(t)
    return ticket_detail(t)


@router.post("/tickets/{ticket_id}/reprocess")
def reprocess(ticket_id: int, db: Session = Depends(get_db), _: Agent = Depends(require_role("supervisor", "admin"))):
    t = _ticket(db, ticket_id)
    pipeline.safe_process(db, t, allow_auto=False)
    return ticket_detail(t)


# ------------------------------------------------------------------ approvals
@router.get("/approvals")
def list_approvals(status: str = "pending", db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    rows = db.scalars(select(Approval).where(Approval.status == status).order_by(Approval.created_at)).all()
    return [approval_dict(a) for a in rows]


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = None
    amount: float | None = None
    notify_customer: bool = True


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: int, body: Decision, db: Session = Depends(get_db), agent: Agent = Depends(current_agent)):
    a = db.get(Approval, approval_id)
    if not a:
        raise HTTPException(404, "approval not found")
    if a.action_type == "refund" and agent.role not in ("supervisor", "admin"):
        raise HTTPException(403, "Refunds must be signed off by a supervisor")
    try:
        approval_service.decide(db, a, body.decision, agent, body.note, body.amount, body.notify_customer)
    except approval_service.ApprovalConflict as e:
        raise HTTPException(409, str(e))
    except actions.ActionError as e:
        raise HTTPException(502, str(e))
    db.refresh(a)
    return approval_dict(a)


# ------------------------------------------------------------------ alerts & queues
@router.get("/alerts")
def alerts(db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    rows = db.scalars(select(Ticket).where(Ticket.safety_flag.is_(True), Ticket.status.in_(TicketStatus.OPEN))
                      .order_by(Ticket.created_at.desc())).all()
    return [ticket_summary(t) for t in rows]


@router.get("/queues/summary")
def queue_summary(db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    rows = db.scalars(select(Ticket).where(Ticket.status.in_(TicketStatus.OPEN))).all()
    now = utcnow()
    teams = {k: {"team": k, "label": v, "open": 0, "P1": 0, "P2": 0, "P3": 0, "P4": 0, "breached": 0, "unassigned": 0}
             for k, v in TEAMS.items()}
    for t in rows:
        row = teams.setdefault(t.team or "customer_support", teams["customer_support"])
        row["open"] += 1
        row[t.priority] = row.get(t.priority, 0) + 1
        if t.sla_due_at and not t.first_response_at and now > t.sla_due_at:
            row["breached"] += 1
        if not t.assigned_agent_id:
            row["unassigned"] += 1
    return {"teams": list(teams.values()), "open_total": len(rows),
            "pending_approvals": db.scalar(select(func.count()).select_from(Approval).where(Approval.status == "pending")),
            "safety_open": sum(1 for t in rows if t.safety_flag)}
