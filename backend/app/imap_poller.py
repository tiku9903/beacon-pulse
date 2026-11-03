"""Optional mailbox poller for support@ inboxes that can't use an inbound webhook.
Enabled when IMAP_HOST is set. Reads UNSEEN mail, creates/appends tickets, marks as seen."""
import email
import imaplib
import logging
import threading
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

from . import pipeline
from .api.channels import _strip_quoted
from .config import get_settings
from .db import SessionLocal

log = logging.getLogger("beacon.imap")


def _text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", "replace")


def poll_once() -> int:
    s = get_settings()
    count = 0
    with imaplib.IMAP4_SSL(s.imap_host) as imap:
        imap.login(s.imap_user, s.imap_password)
        imap.select("INBOX")
        _, data = imap.search(None, "UNSEEN")
        for num in data[0].split():
            _, parts = imap.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(parts[0][1])
            name, addr = parseaddr(msg.get("From", ""))
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            db = SessionLocal()
            try:
                t, _ = pipeline.intake(db, channel="email", body=_strip_quoted(_text(msg)), subject=subject,
                                       email=addr, name=name or None,
                                       external_thread_id=msg.get("In-Reply-To") or msg.get("Message-ID"))
                pipeline.safe_process(db, t)
                count += 1
            except Exception:
                log.exception("could not import message %s", num)
            finally:
                db.close()
                imap.store(num, "+FLAGS", "\\Seen")  # never re-ingest the same email on the next poll
    return count


def start_background_poller() -> threading.Thread | None:
    s = get_settings()
    if not s.imap_host:
        return None

    def loop():
        while True:
            try:
                n = poll_once()
                if n:
                    log.info("imported %d emails", n)
            except Exception:
                log.exception("IMAP poll failed")
            time.sleep(s.imap_poll_seconds)

    th = threading.Thread(target=loop, name="imap-poller", daemon=True)
    th.start()
    return th
