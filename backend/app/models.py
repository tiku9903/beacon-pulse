from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TicketStatus:
    NEW = "new"
    AUTO_RESOLVED = "auto_resolved"          # AI answered, no human touch
    AWAITING_AGENT = "awaiting_agent"        # in an agent queue (usually with an AI draft)
    AWAITING_APPROVAL = "awaiting_approval"  # refund / replacement waiting for sign-off
    AWAITING_CUSTOMER = "awaiting_customer"  # we replied, waiting on customer
    RESOLVED = "resolved"
    CLOSED = "closed"

    OPEN = {NEW, AWAITING_AGENT, AWAITING_APPROVAL}


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="agent")  # agent|supervisor|admin
    skills: Mapped[list] = mapped_column(JSON, default=list)  # team keys, see routing.TEAMS
    max_active_tickets: Mapped[int] = mapped_column(Integer, default=25)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    """Local identity cache, refreshed from the CRM connector on every inbound ticket."""
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    crm_id: Mapped[str | None] = mapped_column(String(80), index=True)
    email: Mapped[str | None] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    segment: Mapped[str] = mapped_column(String(20), default="retail")  # retail|trade
    trade_tier: Mapped[str | None] = mapped_column(String(20))  # silver|gold|platinum
    lifetime_value: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # email|web_form|chat|sms|api
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_phone: Mapped[str | None] = mapped_column(String(40))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"))
    chat_session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(300), index=True)

    status: Mapped[str] = mapped_column(String(30), default=TicketStatus.NEW, index=True)
    # classification
    intent: Mapped[str | None] = mapped_column(String(40), index=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    sentiment: Mapped[str | None] = mapped_column(String(20))
    urgency: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    safety_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    safety_reasons: Mapped[list] = mapped_column(JSON, default=list)
    classification_reasoning: Mapped[str | None] = mapped_column(Text)
    ai_provider: Mapped[str | None] = mapped_column(String(40))
    # routing
    priority: Mapped[str] = mapped_column(String(4), default="P3", index=True)
    team: Mapped[str | None] = mapped_column(String(30), index=True)
    assigned_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime)
    # outcome
    resolution_type: Mapped[str | None] = mapped_column(String(20))  # auto|ai_assisted|manual
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    csat_score: Mapped[int | None] = mapped_column(Integer)
    agent_corrected_intent: Mapped[str | None] = mapped_column(String(40))  # set when an agent reclassifies
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer | None] = relationship()
    assigned_agent: Mapped[Agent | None] = relationship()
    messages: Mapped[list["Message"]] = relationship(back_populates="ticket", order_by="Message.id",
                                                     cascade="all, delete-orphan")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="ticket", order_by="Draft.id",
                                                 cascade="all, delete-orphan")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="ticket", order_by="Approval.id",
                                                       cascade="all, delete-orphan")
    events: Mapped[list["AuditEvent"]] = relationship(back_populates="ticket", order_by="AuditEvent.id",
                                                      cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound|outbound|internal
    author_type: Mapped[str] = mapped_column(String(10))  # customer|ai|agent|system
    author_name: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(20))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ticket: Mapped[Ticket] = relationship(back_populates="messages")


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|discarded
    was_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    kb_sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ticket: Mapped[Ticket] = relationship(back_populates="drafts")


class Approval(Base):
    """Human-in-the-loop gate for money-moving or costly actions."""
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(30))  # refund|replacement|return_label
    amount: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_recommendation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    ticket: Mapped[Ticket] = relationship(back_populates="approvals")
    decided_by: Mapped[Agent | None] = relationship()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), index=True)
    event: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(120), default="system")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ticket: Mapped[Ticket | None] = relationship(back_populates="events")
