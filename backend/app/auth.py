import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import Agent

_bearer = HTTPBearer(auto_error=False)
_ITER = 240_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except ValueError:
        return False


def create_token(agent: Agent) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_expiry_minutes)
    return jwt.encode({"sub": str(agent.id), "role": agent.role, "exp": exp}, s.jwt_secret, algorithm="HS256")


def current_agent(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> Agent:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        data = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    agent = db.get(Agent, int(data["sub"]))
    if not agent or not agent.active:
        raise HTTPException(401, "Account disabled")
    return agent


def require_role(*roles: str):
    def dep(agent: Agent = Depends(current_agent)) -> Agent:
        if agent.role not in roles:
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return agent
    return dep


def csat_token(ref: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), ref.encode(), "sha256").hexdigest()[:16]
