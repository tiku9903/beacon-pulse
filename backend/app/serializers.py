from .ai.taxonomy import TEAMS
from .models import Ticket, TicketStatus, utcnow


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def ticket_summary(t: Ticket) -> dict:
    now = utcnow()
    breached = bool(t.sla_due_at and not t.first_response_at and t.status in TicketStatus.OPEN and now > t.sla_due_at)
    return {
        "id": t.id, "ref": t.ref, "channel": t.channel, "subject": t.subject,
        "preview": (t.body or "")[:160], "status": t.status, "intent": t.intent,
        "intent_confidence": t.intent_confidence, "sentiment": t.sentiment, "urgency": t.urgency,
        "priority": t.priority, "team": t.team, "team_label": TEAMS.get(t.team or "", t.team),
        "safety_flag": t.safety_flag, "safety_reasons": t.safety_reasons,
        "contact_name": t.contact_name, "contact_email": t.contact_email,
        "customer_segment": t.customer.segment if t.customer else None,
        "trade_tier": t.customer.trade_tier if t.customer else None,
        "assigned_agent": {"id": t.assigned_agent.id, "name": t.assigned_agent.name} if t.assigned_agent else None,
        "sla_due_at": _iso(t.sla_due_at), "sla_breached": breached,
        "first_response_at": _iso(t.first_response_at), "resolved_at": _iso(t.resolved_at),
        "resolution_type": t.resolution_type, "csat_score": t.csat_score,
        "pending_approvals": sum(1 for a in t.approvals if a.status == "pending"),
        "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at),
    }


def ticket_detail(t: Ticket) -> dict:
    d = ticket_summary(t)
    c = t.customer
    d.update({
        "body": t.body, "entities": t.entities, "classification_reasoning": t.classification_reasoning,
        "ai_provider": t.ai_provider, "agent_corrected_intent": t.agent_corrected_intent,
        "contact_phone": t.contact_phone,
        "customer": {"id": c.id, "crm_id": c.crm_id, "name": c.name, "email": c.email, "phone": c.phone,
                     "segment": c.segment, "trade_tier": c.trade_tier, "lifetime_value": c.lifetime_value} if c else None,
        "messages": [{"id": m.id, "direction": m.direction, "author_type": m.author_type, "author_name": m.author_name,
                      "body": m.body, "channel": m.channel, "delivered": m.delivered, "created_at": _iso(m.created_at)}
                     for m in t.messages],
        "drafts": [{"id": x.id, "body": x.body, "status": x.status, "was_edited": x.was_edited,
                    "kb_sources": x.kb_sources, "created_at": _iso(x.created_at)} for x in t.drafts],
        "approvals": [approval_dict(a) for a in t.approvals],
        "events": [{"id": e.id, "event": e.event, "actor": e.actor, "data": e.data, "created_at": _iso(e.created_at)}
                   for e in t.events],
    })
    return d


def approval_dict(a) -> dict:
    return {"id": a.id, "ticket_id": a.ticket_id, "ticket_ref": a.ticket.ref if a.ticket else None,
            "action_type": a.action_type, "amount": a.amount, "payload": a.payload,
            "ai_recommendation": a.ai_recommendation, "status": a.status,
            "decided_by": a.decided_by.name if a.decided_by else None, "decision_note": a.decision_note,
            "created_at": _iso(a.created_at), "decided_at": _iso(a.decided_at),
            "contact_name": a.ticket.contact_name if a.ticket else None,
            "intent": a.ticket.intent if a.ticket else None}
