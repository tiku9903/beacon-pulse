import os
import tempfile

_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_db.name}",
    "SEED_DEMO_DATA": "false",
    "AI_PROVIDER": "rules",
    "INBOUND_WEBHOOK_SECRET": "test-secret",
    "JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-ok",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import DEMO_PASSWORD, seed_agents  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_agents(db)
    with TestClient(app) as c:
        yield c


def login(c, email="alex.turner@beaconpulse.demo"):
    r = c.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def sup(client):
    return login(client)


@pytest.fixture()
def settings():
    s = get_settings()
    snapshot = s.model_dump()
    yield s
    for k, v in snapshot.items():
        setattr(s, k, v)
