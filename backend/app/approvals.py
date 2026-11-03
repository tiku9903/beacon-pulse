"""One code path for every approval decision (human or policy auto-approval):
atomic claim -> execute against commerce platform -> notify customer -> update ticket."""
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import actions, responses
from .models import Agent, Approval, Message, TicketStatus, utcnow
from .outbound import log_event, send_reply

CONFIRM = {
    "refund": "Good news - your refund{amt} has been approved and will be processed within 2 business days. "
              "Your bank may take a further 3-5 business days to show the funds.",
    "return": "Your return{amt} has been approved. We'll email a prepaid return label shortly, or you can drop the item "
              "at any Beacon Lighting store. Your refund is released once the item is received.",
    "replacement": "Your warranty claim has been approved and a replacement is on its way. We'll send tracking details "
                   "as soon as it ships.",
}


class ApprovalConflict(RuntimeError):
    pass


def decide(db: Session, a: Approval, decision: str, actor: Agent | None, note: str | None = None,
           amount: float | None = None, notify_customer: bool = True) -> Approval:
    # Atomic claim: only one caller can move pending -> processing, so a double click
    # or two supervisors can never execute the same refund twice.
    claimed = db.execute(update(Approval).where(Approval.id == a.id, Approval.status == "pending")
                         .values(status="processing")).rowcount
    db.commit()
    if not claimed:
        db.refresh(a)
        raise ApprovalConflict(f"already {a.status}")
    db.refresh(a)
    t = a.ticket
    actor_name = actor.name if actor else "Policy (auto-approval)"
    if amount is not None:
        a.amount = amount
    a.decided_by_id, a.decided_at, a.decision_note = (actor.id if actor else None), utcnow(), note

    if decision == "approve":
        try:
            result = actions.execute(a)
        except actions.ActionError:
            a.status, a.decided_by_id, a.decided_at = "pending", None, None  # release the claim
            db.commit()
            raise
        a.status = "approved"
        log_event(db, t, "approval_approved", actor_name, action=a.action_type, amount=a.amount, result=result)
        if result.get("manual_required"):
            t.messages.append(Message(direction="internal", author_type="system", author_name="Beacon Pulse",
                                      body=f"Manual step needed: {result['manual_required']}"))
        if notify_customer:
            amt = f" of ${a.amount:,.2f}" if a.amount else ""
            body = (f"Hi {responses.first_name(t.contact_name)},\n\n"
                    + CONFIRM.get(a.action_type, "Your request has been approved.").format(amt=amt) + responses.SIGN_OFF)
            send_reply(db, t, body, "agent" if actor else "system", actor_name)
    else:
        a.status = "rejected"
        log_event(db, t, "approval_rejected", actor_name, action=a.action_type, note=note)

    if not any(x.status == "pending" for x in t.approvals if x.id != a.id):
        if decision == "approve" and notify_customer:
            t.status, t.resolved_at = TicketStatus.RESOLVED, utcnow()
            t.resolution_type = t.resolution_type or "ai_assisted"
        else:
            t.status = TicketStatus.AWAITING_AGENT
    db.commit()
    return a
