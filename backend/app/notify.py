"""Outbound delivery for email and SMS. Chat replies are stored as messages and picked up
by the widget, so they need no transport."""
import logging
import smtplib
from email.message import EmailMessage

import httpx

from .config import get_settings

log = logging.getLogger("beacon.notify")
OUTBOX: list[dict] = []  # last 200 console-mode sends, visible at /api/admin/outbox


def _console(kind: str, to: str, subject: str | None, body: str) -> bool:
    OUTBOX.append({"kind": kind, "to": to, "subject": subject, "body": body})
    del OUTBOX[:-200]
    log.info("[%s -> %s] %s", kind, to, subject or body[:60])
    return True


def send_email(to: str, subject: str, body: str, thread_id: str | None = None) -> bool:
    s = get_settings()
    if s.email_transport != "smtp":
        return _console("email", to, subject, body)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    if thread_id:
        msg["In-Reply-To"] = msg["References"] = thread_id
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        log.error("SMTP send to %s failed: %s", to, e)
        return False


def send_sms(to: str, body: str) -> bool:
    s = get_settings()
    if s.sms_transport != "twilio":
        return _console("sms", to, None, body)
    try:
        r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
                       auth=(s.twilio_account_sid, s.twilio_auth_token),
                       data={"From": s.twilio_from_number, "To": to, "Body": body[:1500]}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("SMS send to %s failed: %s", to, e)
        return False


def alert_safety_team(ticket_ref: str, summary: str, reasons: list[str]) -> None:
    """Hook for paging (PagerDuty/Teams/Slack webhook). Console by default."""
    _console("safety_alert", "safety-team", f"SAFETY {ticket_ref}", f"{', '.join(reasons)} - {summary}")
