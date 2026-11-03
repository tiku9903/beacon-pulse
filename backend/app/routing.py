"""Phase 4 - Route: priority rules (Safety > Trade/VIP > sentiment), skill-based
assignment to the least-loaded available agent, and SLA due times."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai.taxonomy import INTENT_TEAM
from .models import Agent, Customer, Ticket, TicketStatus, utcnow

# First-response SLA in hours, per priority. 24/7 clock (the automated front line never sleeps).
SLA_HOURS = {"P1": 0.5, "P2": 2.0, "P3": 8.0, "P4": 24.0}
_ORDER = ["P1", "P2", "P3", "P4"]
_TRADE_TEAM_INTENTS = {"order_status", "product_availability", "return_request", "refund_billing", "trade_inquiry", "other"}


def _bump(p: str, steps: int = 1) -> str:
    return _ORDER[max(0, _ORDER.index(p) - steps)]


def decide(classification: dict, customer: Customer | None) -> tuple[str, str, list[str]]:
    """Returns (priority, team, reasons)."""
    intent = classification["intent"]
    why: list[str] = []
    if classification.get("safety_concern"):
        return "P1", "safety", ["safety concern: " + ", ".join(classification.get("safety_reasons", []))]

    team = INTENT_TEAM.get(intent, "customer_support")
    priority = "P4" if intent in ("store_policy", "product_availability", "other") else "P3"

    if customer and customer.segment == "trade":
        if intent in _TRADE_TEAM_INTENTS:
            team = "trade"
        target = "P1" if customer.trade_tier == "platinum" else "P2"
        if _ORDER.index(target) < _ORDER.index(priority):
            priority = target
        why.append(f"trade customer ({customer.trade_tier or 'standard'}) -> priority lane")

    sentiment, urgency = classification.get("sentiment"), classification.get("urgency", 0)
    if sentiment == "angry" or urgency >= 70:
        priority = _bump(priority)
        why.append(f"{sentiment} sentiment / urgency {urgency}")
    if intent in ("warranty_claim", "refund_billing") and priority == "P4":
        priority = "P3"
    return priority, team, why


def pick_agent(db: Session, team: str) -> Agent | None:
    load = (select(Ticket.assigned_agent_id, func.count().label("n"))
            .where(Ticket.status.in_(TicketStatus.OPEN)).group_by(Ticket.assigned_agent_id).subquery())
    rows = db.execute(select(Agent, func.coalesce(load.c.n, 0))
                      .outerjoin(load, load.c.assigned_agent_id == Agent.id)
                      .where(Agent.active.is_(True))).all()
    eligible = [(a, n) for a, n in rows if team in (a.skills or []) and n < a.max_active_tickets]
    if not eligible and team == "safety":  # safety must always land on a supervisor
        eligible = [(a, n) for a, n in rows if a.role in ("supervisor", "admin")]
    if not eligible:
        return None
    return min(eligible, key=lambda x: (x[0].role == "admin", x[1], x[0].id))[0]


def apply(db: Session, ticket: Ticket, classification: dict) -> list[str]:
    priority, team, why = decide(classification, ticket.customer)
    ticket.priority, ticket.team = priority, team
    ticket.sla_due_at = (ticket.created_at or utcnow()) + timedelta(hours=SLA_HOURS[priority])
    return why
