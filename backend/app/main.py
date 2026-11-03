import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ai.providers import get_provider
from .api import admin, analytics, channels, console
from .config import get_settings
from .db import Base, SessionLocal, engine
from .imap_poller import start_background_poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("beacon")
STATIC = Path(__file__).resolve().parent.parent / "static"  # built frontend (Docker image copies it here)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.environment == "production":
        weak = [n for n, v, min_len in (("JWT_SECRET", s.jwt_secret, 32), ("INBOUND_WEBHOOK_SECRET", s.inbound_webhook_secret, 16))
                if len(v) < min_len or any(x in v for x in ("dev-only", "change-me", "replace-with"))]
        if weak:
            # Never run with a guessable secret, but don't refuse to start either: use a random one.
            # (Console logins reset on restart and inbound webhooks stay closed until a real value is set.)
            import secrets
            s.jwt_secret = s.jwt_secret if "JWT_SECRET" not in weak else secrets.token_urlsafe(48)
            if "INBOUND_WEBHOOK_SECRET" in weak:
                s.inbound_webhook_secret = secrets.token_urlsafe(32)
            log.warning("%s missing or weak - generated a random value for this run. Set it in the host's "
                        "environment settings to keep it stable.", ", ".join(weak))
    Base.metadata.create_all(engine)
    if s.seed_demo_data:
        from .seed import seed_all
        with SessionLocal() as db:
            log.info("demo seed: %s", seed_all(db))
    p = get_provider()
    log.info("AI provider: %s | automation mode: %s | auto-resolve intents: %s",
             p.name if p else "rules (no LLM key)", s.automation_mode, sorted(s.auto_resolve_intent_set))
    start_background_poller()
    yield


app = FastAPI(title="Beacon Pulse API", version="1.0.0", lifespan=lifespan,
              description="AI-powered customer service automation for Beacon Lighting.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (channels.router, console.router, analytics.router, admin.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    p = get_provider()
    return {"status": "ok", "ai_provider": p.name if p else "rules", "mode": get_settings().automation_mode,
            "demo": get_settings().seed_demo_data}


if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    _root = STATIC.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (STATIC / path).resolve()
        # containment check: never serve anything outside the built frontend
        if path and f.is_relative_to(_root) and f.is_file():
            return FileResponse(f)
        return FileResponse(_root / "index.html")
