"""Sending a reply on whatever channel the ticket came in on, and the audit trail."""
from sqlalchemy.orm import Session

from . import notify
from .models import AuditEvent, Message, Ticket, utcnow


def log_event(db: Session, ticket: Ticket | None, event: str, actor: str = "system", **data) -> None:
    db.add(AuditEvent(ticket_id=ticket.id if ticket else None, event=event, actor=actor, data=data))


def send_reply(db: Session, ticket: Ticket, body: str, author_type: str, author_name: str,
               counts_as_response: bool = True, channel: str | None = None, subject: str | None = None) -> Message:
    delivered = True
    channel = channel or ticket.channel
    if channel in ("email", "web_form", "api") and ticket.contact_email:
        subject = f"{subject or 'Re: ' + (ticket.subject or 'Your Beacon Lighting enquiry')} [{ticket.ref}]"
        delivered = notify.send_email(ticket.contact_email, subject, body, ticket.external_thread_id)
    elif channel == "sms" and ticket.contact_phone:
        delivered = notify.send_sms(ticket.contact_phone, body)
    # chat: the widget polls /api/chat/{session}/messages, so storing the message is delivery
    msg = Message(direction="outbound", author_type=author_type, author_name=author_name,
                  body=body, channel=channel, delivered=delivered)
    ticket.messages.append(msg)
    if counts_as_response and not ticket.first_response_at:
        ticket.first_response_at = utcnow()
    log_event(db, ticket, "reply_sent", author_name, author_type=author_type, delivered=delivered,
              counts_as_response=counts_as_response)
    return msg
