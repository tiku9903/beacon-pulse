"""Phase 1 - Intake. All channels land in one smart queue."""
import base64
import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import pipeline
from ..auth import csat_token
from ..config import get_settings
from ..db import get_db
from ..models import Message, Ticket
from ..outbound import log_event

router = APIRouter(prefix="/api", tags=["channels"])


class WebForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = None
    subject: str | None = Field(default=None, max_length=300)
    message: str = Field(min_length=3, max_length=8000)
    order_number: str | None = None


@router.post("/channels/web-form", status_code=201)
def web_form(body: WebForm, bg: BackgroundTasks, db: Session = Depends(get_db)):
    text = body.message + (f"\n\nOrder number: {body.order_number}" if body.order_number else "")
    t, _ = pipeline.intake(db, channel="web_form", body=text, subject=body.subject, email=body.email,
                           name=body.name, phone=body.phone)
    bg.add_task(pipeline.process_by_id, t.id)
    return {"ref": t.ref, "message": f"Thanks {body.name.split()[0]}, we've received your enquiry ({t.ref})."}


class InboundEmail(BaseModel):
    """Normalised inbound-email payload. Map your provider's inbound-parse webhook onto this
    (SendGrid Inbound Parse, Mailgun Routes, Postmark Inbound, AWS SES -> Lambda)."""
    from_email: EmailStr
    from_name: str | None = None
    subject: str | None = None
    text: str
    message_id: str | None = None
    in_reply_to: str | None = None


@router.post("/channels/email/inbound", status_code=202)
def inbound_email(body: InboundEmail, bg: BackgroundTasks, db: Session = Depends(get_db),
                  x_webhook_secret: str | None = Header(default=None)):
    if not hmac.compare_digest(x_webhook_secret or "", get_settings().inbound_webhook_secret):
        raise HTTPException(401, "bad webhook secret")
    t, _ = pipeline.intake(db, channel="email", body=_strip_quoted(body.text), subject=body.subject,
                           email=body.from_email, name=body.from_name,
                           external_thread_id=body.in_reply_to or body.message_id)
    if not t.external_thread_id and body.message_id:
        t.external_thread_id = body.message_id
        db.commit()
    bg.add_task(pipeline.process_by_id, t.id)
    return {"ref": t.ref}


def _twilio_signature_ok(request: Request, params: dict, signature: str | None) -> bool:
    """https://www.twilio.com/docs/usage/security#validating-requests"""
    token = get_settings().twilio_auth_token
    if not token:
        return True  # signature check enabled automatically once TWILIO_AUTH_TOKEN is set
    url = str(request.url)
    if request.headers.get("x-forwarded-proto") == "https":
        url = url.replace("http://", "https://", 1)
    payload = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature or "")


@router.post("/channels/sms/inbound", status_code=202)
async def inbound_sms(request: Request, bg: BackgroundTasks, secret: str = "", db: Session = Depends(get_db)):
    """Twilio Messaging webhook: https://<host>/api/channels/sms/inbound?secret=<INBOUND_WEBHOOK_SECRET>"""
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not hmac.compare_digest(secret, get_settings().inbound_webhook_secret):
        raise HTTPException(401, "bad webhook secret")
    if not _twilio_signature_ok(request, form, request.headers.get("x-twilio-signature")):
        raise HTTPException(401, "bad Twilio signature")
    if not form.get("From") or not form.get("Body"):
        raise HTTPException(422, "From and Body are required")
    t, _ = pipeline.intake(db, channel="sms", body=form["Body"], phone=form["From"],
                           external_thread_id=f"sms:{form['From']}")
    bg.add_task(pipeline.process_by_id, t.id)
    return {"ref": t.ref}


def _strip_quoted(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.startswith(">") or (line.startswith("On ") and line.rstrip().endswith("wrote:")):
            break
        out.append(line)
    return "\n".join(out).strip() or text


# ------------------------------------------------------------------ live chat
class ChatStart(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sign_session(session_id: str, name: str, email: str) -> str:
    """Stateless chat token binding the session to the identity given at start - the email
    can't be swapped mid-conversation to read someone else's ticket."""
    payload = _b64(json.dumps({"s": session_id, "n": name, "e": email.lower()}).encode())
    sig = _b64(hmac.new(get_settings().jwt_secret.encode(), f"chat.{payload}".encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _chat_identity(session_id: str, token: str | None) -> dict:
    try:
        payload, sig = (token or "").split(".")
        expected = _b64(hmac.new(get_settings().jwt_secret.encode(), f"chat.{payload}".encode(), hashlib.sha256).digest())
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        raise HTTPException(401, "invalid chat session")
    if not hmac.compare_digest(sig, expected) or data.get("s") != session_id:
        raise HTTPException(401, "invalid chat session")
    return data


@router.post("/chat/sessions", status_code=201)
def chat_start(body: ChatStart):
    sid = uuid.uuid4().hex
    return {"session_id": sid, "token": _sign_session(sid, body.name, body.email), "name": body.name, "email": body.email,
            "greeting": f"Hi {body.name.split()[0]}! I'm the Beacon Lighting assistant. Ask about an order, stock, "
                        "returns, warranty or anything else - a specialist is here if you need one."}


@router.post("/chat/{session_id}/messages")
def chat_send(session_id: str, body: ChatMessage, db: Session = Depends(get_db),
              x_chat_token: str | None = Header(default=None)):
    who = _chat_identity(session_id, x_chat_token)
    t, _ = pipeline.intake(db, channel="chat", body=body.message, email=who["e"], name=who["n"],
                           subject="Live chat", chat_session_id=session_id)
    pipeline.safe_process(db, t)  # inline: the customer is waiting on the other end
    return chat_messages(session_id, 0, db, x_chat_token)


@router.get("/chat/{session_id}/messages")
def chat_messages(session_id: str, after_id: int = 0, db: Session = Depends(get_db),
                  x_chat_token: str | None = Header(default=None)):
    _chat_identity(session_id, x_chat_token)
    t = db.scalar(select(Ticket).where(Ticket.chat_session_id == session_id).order_by(Ticket.id.desc()))
    if not t:
        return {"ticket_ref": None, "status": None, "messages": []}
    ids = db.scalars(select(Ticket.id).where(Ticket.chat_session_id == session_id)).all()
    # only messages that belong in the chat window (email copies of private details stay in the inbox)
    msgs = db.scalars(select(Message).where(Message.ticket_id.in_(ids), Message.id > after_id,
                                            Message.direction != "internal", Message.channel == "chat")
                      .order_by(Message.id)).all()
    return {"ticket_ref": t.ref, "status": t.status, "csat_token": csat_token(t.ref),
            "messages": [{"id": m.id, "from": "customer" if m.direction == "inbound" else
                          ("agent" if m.author_type == "agent" else "bot"),
                          "author": m.author_name, "body": m.body, "created_at": m.created_at.isoformat() + "Z"}
                         for m in msgs]}


# ------------------------------------------------------------------ CSAT
class CSAT(BaseModel):
    score: int = Field(ge=1, le=5)
    token: str
    comment: str | None = Field(default=None, max_length=2000)


@router.post("/csat/{ref}")
def submit_csat(ref: str, body: CSAT, db: Session = Depends(get_db)):
    t = db.scalar(select(Ticket).where(Ticket.ref == ref))
    if not t or body.token != csat_token(ref):
        raise HTTPException(404, "ticket not found")
    t.csat_score = body.score
    log_event(db, t, "csat", "customer", score=body.score, comment=body.comment)
    db.commit()
    return {"ok": True}
