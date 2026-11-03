from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import notify
from ..ai.providers import get_provider
from ..auth import current_agent, require_role
from ..config import get_settings
from ..connectors import crm_connector, inventory_connector, order_connector
from ..db import get_db
from ..knowledge import kb
from ..models import Agent, AuditEvent, Ticket

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/config")
def config(_: Agent = Depends(current_agent)):
    s = get_settings()
    p = get_provider()
    return {
        "environment": s.environment,
        "ai_provider_configured": s.ai_provider,
        "ai_provider_active": p.name if p else "rules",
        "ai_models": {"anthropic": [s.anthropic_model, s.anthropic_fast_model], "openai": [s.openai_model, s.openai_fast_model],
                      "groq": [s.groq_model, s.groq_fast_model], "gemini": [s.gemini_model, s.gemini_fast_model]},
        "automation_mode": s.automation_mode,
        "agent_mode": bool(s.agent_mode and p),
        "auto_resolve_intents": sorted(s.auto_resolve_intent_set),
        "auto_resolve_min_confidence": s.auto_resolve_min_confidence,
        "refund_auto_approve_limit": s.refund_auto_approve_limit,
        "connectors": {"orders": order_connector().name, "crm": crm_connector().name,
                       "inventory": inventory_connector().name},
        "email_transport": s.email_transport, "sms_transport": s.sms_transport,
        "knowledge_passages": len(kb().passages),
    }


@router.post("/knowledge/reload")
def reload_kb(_: Agent = Depends(require_role("admin", "supervisor"))):
    return {"passages": kb().reload()}


@router.get("/knowledge")
def list_kb(_: Agent = Depends(current_agent)):
    return [{"id": p.id, "doc": p.doc, "title": p.title, "text": p.text} for p in kb().passages]


@router.get("/outbox")
def outbox(_: Agent = Depends(current_agent)):
    """Messages 'sent' while email/SMS transports are in console mode (demo & UAT)."""
    return list(reversed(notify.OUTBOX))


@router.post("/demo-data/purge")
def purge_demo(db: Session = Depends(get_db), _: Agent = Depends(require_role("admin"))):
    ids = db.scalars(select(Ticket.id).where(Ticket.is_demo.is_(True))).all()
    db.execute(delete(AuditEvent).where(AuditEvent.ticket_id.in_(ids)))
    for t in db.scalars(select(Ticket).where(Ticket.id.in_(ids))):
        db.delete(t)
    db.commit()
    return {"deleted_tickets": len(ids)}
