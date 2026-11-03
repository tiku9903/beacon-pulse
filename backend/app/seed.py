"""Demo data: the support team (7 specialists, as in the business case) and 30 days of
realistic ticket history run through the real pipeline, plus a live open queue.
All demo tickets have is_demo=True and can be purged from Admin before go-live."""
import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import pipeline
from .ai import engine
from .auth import hash_password
from .models import Agent, Ticket, TicketStatus, utcnow

DEMO_PASSWORD = "BeaconDemo!2026"

AGENTS = [
    ("Pulse Admin", "admin@beaconpulse.demo", "admin", ["customer_support", "returns", "warranty", "technical", "trade", "safety"]),
    ("Alex Turner", "alex.turner@beaconpulse.demo", "supervisor", ["safety", "customer_support", "returns", "trade"]),
    ("Emma Collins", "emma.collins@beaconpulse.demo", "agent", ["customer_support"]),
    ("Liam Patel", "liam.patel@beaconpulse.demo", "agent", ["customer_support", "returns"]),
    ("Jack Wilson", "jack.wilson@beaconpulse.demo", "agent", ["returns", "customer_support"]),
    ("Mia Robinson", "mia.robinson@beaconpulse.demo", "agent", ["warranty", "technical"]),
    ("Noah Kelly", "noah.kelly@beaconpulse.demo", "agent", ["technical", "warranty"]),
    ("Olivia Brown", "olivia.brown@beaconpulse.demo", "agent", ["trade", "customer_support"]),
    ("Chloe Nguyen", "chloe.nguyen@beaconpulse.demo", "agent", ["customer_support", "technical"]),
]

KNOWN = [("Sarah Mitchell", "sarah.mitchell@example.com"), ("James O'Connor", "james.oconnor@example.com"),
         ("Priya Raman", "priya.raman@example.com"), ("Tom Nguyen", "tom.nguyen@example.com"),
         ("Bright Spark Electrical", "accounts@brightspark.example"), ("Coastline Builders", "orders@coastlinebuilders.example")]
FIRST = ["Olivia", "Noah", "Charlotte", "Jack", "Amelia", "William", "Isla", "Henry", "Ava", "Leo", "Grace", "Lucas",
         "Ruby", "Oscar", "Zoe", "Ethan", "Harper", "Mason", "Ella", "Hugo"]
LAST = ["Smith", "Jones", "Williams", "Brown", "Wilson", "Taylor", "Martin", "Anderson", "Thompson", "White", "Lee",
        "Harris", "Clarke", "Walker", "Hall", "Young", "King", "Wright", "Scott", "Green"]

SAMPLES = {
    "order_status": [
        "Hi, where is my order? It's been a few days and I haven't had an update.",
        "Can you tell me when my ceiling fan will arrive? Order {order}",
        "Has order {order} shipped yet? I need it before the electrician comes on Friday.",
        "Is my click and collect order ready to pick up?",
        "Still no delivery for {order}. This is getting frustrating.",
    ],
    "product_availability": [
        "Do you have the Aero 52\" DC ceiling fan in stock at Richmond?",
        "Is the Breeze 48 ceiling fan in matt black available anywhere? It says sold out online.",
        "When will the Harbour exterior wall light be back in stock?",
        "Do you have the tri-colour LED downlight 10 pack in stock?",
        "Is the Coastal Rattan pendant available in Brisbane stores?",
    ],
    "store_policy": [
        "What are your store trading hours on the weekend?",
        "Do you price match? I found the same pendant cheaper elsewhere.",
        "How long does delivery usually take to regional Victoria?",
        "What is your return policy for change of mind?",
        "How long is the warranty period on ceiling fans?",
    ],
    "return_request": [
        "I'd like to return the pendant from order {order}, it doesn't match our kitchen.",
        "Wrong colour arrived, I want to exchange it. Order {order}.",
        "Can I return an unopened downlight pack? Change of mind.",
    ],
    "refund_billing": [
        "I was charged twice for order {order}. Please refund the duplicate payment of $129.00.",
        "When will I get my refund for the fan I returned last week?",
        "My invoice for {order} looks wrong, I was overcharged.",
    ],
    "warranty_claim": [
        "My ceiling fan from order {order} has stopped working. The light still works but the fan won't spin.",
        "The LED downlights are faulty, three of them died after a month.",
        "Fan remote is dead and the fan won't turn on at all, it's under warranty. Order {order}",
    ],
    "technical_support": [
        "My smart bulb won't connect to wi-fi in the app. How do I reset it?",
        "The new downlights flicker when I use the dimmer. Any ideas?",
        "The ceiling fan is wobbling a lot on high speed, how do I balance it?",
        "How do I pair a new remote to my fan?",
    ],
    "trade_inquiry": [
        "We need a quote for 120 LED downlights and 30 track kits for a commercial fit-out in the CBD.",
        "How do I open a trade account? I'm a licensed electrician.",
        "Can you reserve stock for a project and deliver to site in two drops?",
    ],
    "safety": [
        "URGENT: there's a burning smell coming from the ceiling fan and it's sparking near the canopy.",
        "My LED driver melted and there's scorch marks on the ceiling. Is this product recalled?",
        "Got a shock touching the metal wall light outside. Is this safe?",
    ],
    "other": [
        "Just wanted to say thanks to the Moorabbin team, great service!",
        "Do you have any jobs available in your Perth store?",
    ],
}
WEIGHTS = {"order_status": 30, "product_availability": 12, "store_policy": 12, "return_request": 9, "refund_billing": 7,
           "warranty_claim": 9, "technical_support": 10, "trade_inquiry": 6, "safety": 2, "other": 3}
ORDERS = ["BL100045821", "BL100045822", "BL100045823", "BL100045824", "BL100045825", "BL100045826",
          "BL100045827", "BL100045828"]
ORDER_OWNER = {"BL100045821": 0, "BL100045827": 0, "BL100045822": 1, "BL100045823": 2, "BL100045826": 2,
               "BL100045824": 3, "BL100045825": 4, "BL100045828": 5}


def seed_agents(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(Agent)):
        return
    for name, email, role, skills in AGENTS:
        db.add(Agent(name=name, email=email, role=role, skills=skills, password_hash=hash_password(DEMO_PASSWORD)))
    db.commit()


def _message(rng: random.Random, kind: str):
    text = rng.choice(SAMPLES[kind])
    if "{order}" in text:
        order = rng.choice(ORDERS)
        name, email = KNOWN[ORDER_OWNER[order]]
        return text.format(order=order), name, email
    if rng.random() < 0.35:
        name, email = rng.choice(KNOWN)
    else:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.lower().replace(' ', '.')}{rng.randint(1, 99)}@example.com"
    return text, name, email


def _subject(text: str, limit: int = 60) -> str:
    first = text.split(". ")[0].split("? ")[0].rstrip(".?!")
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0] + "…"


def _shift(t: Ticket, delta: timedelta) -> None:
    for attr in ("created_at", "sla_due_at", "first_response_at", "resolved_at"):
        v = getattr(t, attr)
        if v:
            setattr(t, attr, v + delta)
    for coll in (t.messages, t.drafts, t.approvals, t.events):
        for x in coll:
            x.created_at = x.created_at + delta
    for a in t.approvals:
        if a.decided_at:
            a.decided_at += delta


def _simulate_agent(db: Session, rng: random.Random, t: Ticket, agents: list[Agent]) -> None:
    """Plays out what the team did with a queued ticket (seed history only)."""
    hours = {"P1": (0.1, 0.6), "P2": (0.3, 2.2), "P3": (0.5, 7.5), "P4": (1, 20)}[t.priority]
    delay = timedelta(hours=rng.uniform(*hours) if rng.random() > 0.06 else rng.uniform(hours[1], hours[1] * 2.5))
    pool = [a for a in agents if t.team in (a.skills or [])] or agents
    agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else rng.choice(pool)
    t.assigned_agent_id = agent.id
    if rng.random() < 0.06 and t.intent not in ("other",):
        t.agent_corrected_intent = rng.choice(["technical_support", "warranty_claim", "store_policy"])
        pipeline.log_event(db, t, "reclassified", agent.name, from_intent=t.intent, to_intent=t.agent_corrected_intent)
    draft = next((d for d in reversed(t.drafts) if d.status == "pending"), None)
    use_draft = draft is not None and rng.random() < 0.82
    if use_draft:
        draft.status, draft.was_edited = "sent", rng.random() < 0.3
    for d in t.drafts:
        if d.status == "pending":
            d.status = "superseded"
    first = t.first_response_at
    t.first_response_at = first if first and first - t.created_at < delay else t.created_at + delay
    t.resolution_type = "ai_assisted" if use_draft else "manual"
    for a in t.approvals:
        if a.status == "pending":
            a.status = "approved" if rng.random() < 0.85 else "rejected"
            a.decided_by_id = next((x.id for x in agents if x.role == "supervisor"), agent.id)
            a.decided_at = t.created_at + delay + timedelta(minutes=rng.randint(5, 90))
    t.status, t.resolved_at = TicketStatus.RESOLVED, t.created_at + delay + timedelta(minutes=rng.randint(2, 240))


def _csat(rng: random.Random, t: Ticket) -> None:
    if rng.random() > 0.38:
        return
    base = {"auto": 4.7, "ai_assisted": 4.5, "manual": 4.0}.get(t.resolution_type, 4.0)
    if t.first_response_at and (t.first_response_at - t.created_at) > timedelta(hours=8):
        base -= 1.2
    t.csat_score = max(1, min(5, round(rng.gauss(base, 0.6))))


def seed_history(db: Session, days: int = 30, per_day: tuple[int, int] = (9, 16), seed: int = 7) -> int:
    if db.scalar(select(func.count()).select_from(Ticket)):
        return 0
    rng = random.Random(seed)
    original = engine.get_provider
    engine.get_provider = lambda: None  # history is generated with the deterministic engine (fast, free)
    try:
        agents = db.scalars(select(Agent).where(Agent.role != "admin")).all()
        kinds, weights = zip(*WEIGHTS.items())
        now = utcnow()
        made = 0
        for day in range(days, 0, -1):
            for _ in range(rng.randint(*per_day)):
                kind = rng.choices(kinds, weights)[0]
                text, name, email = _message(rng, kind)
                channel = rng.choices(["email", "web_form", "chat"], [45, 30, 25])[0]
                t, _ = pipeline.intake(db, channel=channel, body=text, email=email, name=name,
                                       subject=None if channel == "chat" else _subject(text),
                                       chat_session_id=f"demo-{made}" if channel == "chat" else None)
                t.is_demo = True
                pipeline.process(db, t)
                created = now - timedelta(days=day, hours=-rng.uniform(0, 23.9))
                _shift(t, created - t.created_at)
                if t.status in (TicketStatus.AUTO_RESOLVED,):
                    t.first_response_at = t.created_at + timedelta(seconds=rng.randint(4, 40))
                    t.resolved_at = t.first_response_at
                elif t.status == TicketStatus.AWAITING_CUSTOMER:  # asked for an order number, customer never replied
                    t.first_response_at = t.created_at + timedelta(seconds=rng.randint(4, 40))
                    t.status, t.resolved_at, t.resolution_type = TicketStatus.CLOSED, t.created_at + timedelta(days=3), "auto"
                elif day > 1 or rng.random() < 0.5:
                    _simulate_agent(db, rng, t, agents)
                _csat(rng, t)
                made += 1
                db.commit()
        return made
    finally:
        engine.get_provider = original


LIVE_QUEUE = [
    ("email", "Sarah Mitchell", "sarah.mitchell@example.com",
     "There's a burning smell from my Breeze fan and I saw sparks near the light. Order BL100045827. Kids are home, please help!"),
    ("email", "Bright Spark Electrical", "accounts@brightspark.example",
     "Need 200 more Trio downlight 10-packs and 12 Linea track kits for the Collins St fit-out by next Thursday. Can you reserve stock across Melbourne stores and quote?"),
    ("web_form", "James O'Connor", "james.oconnor@example.com",
     "I was charged twice for order BL100045822. The duplicate charge is $258.00 - please refund it."),
    ("email", "Priya Raman", "priya.raman@example.com",
     "My Aero ceiling fan from order BL100045826 has stopped spinning - the light works but the motor is dead."),
    ("chat", "Tom Nguyen", "tom.nguyen@example.com",
     "The downlights from my last order flicker like crazy with my dimmer. Is it the dimmer or the lights?"),
    ("web_form", "Grace Hall", "grace.hall12@example.com",
     "Absolutely ridiculous - third email this week and nobody has told me where my fan is. Never shopping here again!!"),
    ("email", "Coastline Builders", "orders@coastlinebuilders.example",
     "Our order BL100045828 for 18 Harbour wall lights is still processing. Site handover is in 2 weeks - what's the ETA?"),
    ("web_form", "Priya Raman", "priya.raman@example.com",
     "I'd like to return the 2 rattan pendants from order BL100045823 - change of mind, still boxed."),
]


def seed_live_queue(db: Session) -> None:
    for i, (channel, name, email, text) in enumerate(LIVE_QUEUE):
        t, _ = pipeline.intake(db, channel=channel, body=text, email=email, name=name,
                               subject=None if channel == "chat" else _subject(text),
                               chat_session_id=f"demo-live-{i}" if channel == "chat" else None)
        t.is_demo = True
        db.commit()
        pipeline.process(db, t)
        _shift(t, timedelta(minutes=-rng_minutes(i)))
        db.commit()


def rng_minutes(i: int) -> int:
    return [6, 14, 22, 35, 3, 48, 70, 95][i % 8]


def seed_all(db: Session) -> dict:
    seed_agents(db)
    if db.scalar(select(func.count()).select_from(Ticket)):
        return {"seeded": False}
    original = engine.get_provider
    n = seed_history(db)
    engine.get_provider = lambda: None
    try:
        seed_live_queue(db)
    finally:
        engine.get_provider = original
    return {"seeded": True, "history_tickets": n, "live_tickets": len(LIVE_QUEUE)}
