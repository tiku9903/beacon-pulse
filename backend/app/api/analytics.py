"""Success criteria from the deck, measured live: automation mix, response time, SLA,
classification accuracy, CSAT and the $ savings model."""
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import current_agent
from ..config import get_settings
from ..db import get_db
from ..models import Agent, AuditEvent, Ticket, utcnow

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
AEST = ZoneInfo("Australia/Melbourne")
TARGETS = {"automation_rate": 0.60, "assisted_rate": 0.30, "avg_first_response_hours": 1.2,
           "sla_compliance": 0.95, "classification_accuracy": 0.90, "csat": 4.8}


def cost_model(auto_rate: float, assisted_rate: float) -> dict:
    """Annualised cost at the measured automation mix (see docs/ARCHITECTURE.md#savings-model).
    Labour scales with the share of tickets a human still touches; an AI-drafted ticket costs
    `assisted_effort_ratio` of a fully manual one."""
    s = get_settings()
    labour = s.baseline_annual_cost * (1 - s.overhead_share)
    overhead = s.baseline_annual_cost * s.overhead_share
    manual_rate = max(0.0, 1 - auto_rate - assisted_rate)
    touch = manual_rate + assisted_rate * s.assisted_effort_ratio
    projected = labour * touch + overhead + s.platform_annual_cost
    per_ticket = labour / s.baseline_annual_tickets
    return {
        "baseline_annual_cost": s.baseline_annual_cost,
        "projected_annual_cost": round(projected),
        "annual_savings": round(s.baseline_annual_cost - projected),
        "cost_reduction": round(1 - projected / s.baseline_annual_cost, 3),
        "monthly_cost_of_delay": round((s.baseline_annual_cost - projected) / 12),
        "labour_cost_per_manual_ticket": round(per_ticket, 2),
        "human_touch_equivalent": round(touch, 3),
        "fte_equivalent_now": round(27 * touch, 1),
        "assumptions": {"overhead_share": s.overhead_share, "assisted_effort_ratio": s.assisted_effort_ratio,
                        "platform_annual_cost": s.platform_annual_cost,
                        "baseline_annual_tickets": s.baseline_annual_tickets, "baseline_headcount": 27},
    }


def _is_after_hours(dt) -> bool:
    local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(AEST)
    return local.weekday() >= 5 or not (9 <= local.hour < 17)


@router.get("/overview")
def overview(days: int = 30, include_demo: bool = True, db: Session = Depends(get_db),
             _: Agent = Depends(current_agent)):
    s = get_settings()
    since = utcnow() - timedelta(days=days)
    stmt = select(Ticket).where(Ticket.created_at >= since).options(selectinload(Ticket.drafts))
    if not include_demo:
        stmt = stmt.where(Ticket.is_demo.is_(False))
    tickets = db.scalars(stmt).all()
    n = len(tickets)

    res = Counter(t.resolution_type for t in tickets if t.resolution_type)
    resolved = sum(res.values())
    auto_rate = res["auto"] / resolved if resolved else 0
    assisted_rate = res["ai_assisted"] / resolved if resolved else 0

    frt = [(t.first_response_at - t.created_at).total_seconds() / 3600 for t in tickets if t.first_response_at]
    sla_pool = [t for t in tickets if t.sla_due_at and (t.first_response_at or utcnow() > t.sla_due_at)]
    sla_ok = sum(1 for t in sla_pool if t.first_response_at and t.first_response_at <= t.sla_due_at)
    csat = [t.csat_score for t in tickets if t.csat_score]
    classified = [t for t in tickets if t.intent]
    corrected = sum(1 for t in classified if t.agent_corrected_intent)
    sent_drafts = [d for t in tickets for d in t.drafts if d.status == "sent"]
    after_hours = [t for t in tickets if _is_after_hours(t.created_at)]
    ah_handled = [t for t in after_hours if t.first_response_at and (t.first_response_at - t.created_at) <= timedelta(hours=1)]

    daily = defaultdict(lambda: {"total": 0, "auto": 0, "ai_assisted": 0, "manual": 0, "frt": []})
    for t in tickets:
        d = daily[t.created_at.date().isoformat()]
        d["total"] += 1
        if t.resolution_type in ("auto", "ai_assisted", "manual"):
            d[t.resolution_type] += 1
        if t.first_response_at:
            d["frt"].append((t.first_response_at - t.created_at).total_seconds() / 3600)
    series = [{"date": k, "total": v["total"], "auto": v["auto"], "ai_assisted": v["ai_assisted"],
               "manual": v["manual"], "avg_first_response_hours": round(statistics.mean(v["frt"]), 2) if v["frt"] else None}
              for k, v in sorted(daily.items())]

    by_intent = defaultdict(lambda: {"total": 0, "auto": 0, "ai_assisted": 0, "manual": 0, "corrected": 0})
    for t in tickets:
        row = by_intent[t.intent or "unclassified"]
        row["total"] += 1
        if t.resolution_type in row:
            row[t.resolution_type] += 1
        row["corrected"] += 1 if t.agent_corrected_intent else 0

    def r(x, k=3):
        return round(x, k) if x is not None else None

    return {
        "window_days": days, "tickets": n, "resolved": resolved,
        "open": sum(1 for t in tickets if t.status in ("new", "awaiting_agent", "awaiting_approval")),
        "safety_alerts": sum(1 for t in tickets if t.safety_flag),
        "resolution_mix": {"auto": res["auto"], "ai_assisted": res["ai_assisted"], "manual": res["manual"]},
        "automation_rate": r(auto_rate), "assisted_rate": r(assisted_rate),
        "avg_first_response_hours": r(statistics.mean(frt), 2) if frt else None,
        "median_first_response_hours": r(statistics.median(frt), 2) if frt else None,
        "p90_first_response_hours": r(sorted(frt)[int(len(frt) * 0.9) - 1], 2) if len(frt) >= 10 else None,
        "sla_compliance": r(sla_ok / len(sla_pool)) if sla_pool else None,
        "csat": r(statistics.mean(csat), 2) if csat else None, "csat_responses": len(csat),
        "classification_accuracy": r(1 - corrected / len(classified)) if classified else None,
        "draft_acceptance_unedited": r(sum(1 for d in sent_drafts if not d.was_edited) / len(sent_drafts)) if sent_drafts else None,
        "after_hours_tickets": len(after_hours),
        "after_hours_answered_within_1h": r(len(ah_handled) / len(after_hours)) if after_hours else None,
        "by_channel": dict(Counter(t.channel for t in tickets)),
        "by_intent": dict(by_intent), "by_priority": dict(Counter(t.priority for t in tickets)),
        "daily": series,
        "baseline": {"avg_first_response_hours": s.baseline_response_hours, "csat": s.baseline_csat,
                     "annual_cost": s.baseline_annual_cost, "annual_tickets": s.baseline_annual_tickets},
        "targets": TARGETS,
        "savings": cost_model(auto_rate, assisted_rate),
        "savings_at_target": cost_model(TARGETS["automation_rate"], TARGETS["assisted_rate"]),
    }


@router.get("/corrections")
def corrections(limit: int = 100, db: Session = Depends(get_db), _: Agent = Depends(current_agent)):
    """Agent intent corrections - the labelled dataset for tuning prompts or fine-tuning."""
    rows = db.scalars(select(AuditEvent).where(AuditEvent.event == "reclassified")
                      .order_by(AuditEvent.id.desc()).limit(limit)).all()
    out = []
    for e in rows:
        t = e.ticket
        out.append({"ticket_ref": t.ref if t else None, "text": t.body if t else None, **e.data,
                    "by": e.actor, "at": e.created_at.isoformat() + "Z"})
    return out
