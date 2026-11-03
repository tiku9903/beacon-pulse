"""Agent engine tests with a scripted LLM: each test plays the model's tool calls and checks what
the system actually does (tools, guardrails, approvals, ticket state, what the customer sees)."""
import itertools

import pytest

from app.ai import engine
from app.ai.providers import AssistantTurn, ProviderError, ToolCall
from tests.conftest import login

_ids = itertools.count()


class ScriptedLLM:
    """Plays a fixed list of turns. A turn is a list of (tool, args) calls, or a plain string."""
    name = "scripted"
    model = "scripted-model"

    def __init__(self, *turns, fail=False):
        self.turns, self.fail = list(turns), fail
        self.tools_seen: list[list[str]] = []
        self.messages_seen: list[list[dict]] = []

    def structured(self, *a, **k):
        raise ProviderError("classification via rules in tests")

    def text(self, *a, **k):
        raise ProviderError("drafts via templates in tests")

    def chat(self, messages, tools, fast=False):
        if self.fail:
            raise ProviderError("429 rate limited")
        self.tools_seen.append([t["function"]["name"] for t in tools])
        self.messages_seen.append([dict(m) for m in messages])
        turn = self.turns.pop(0) if self.turns else "Sorry, I couldn't finish that."
        if isinstance(turn, str):
            return AssistantTurn(turn, [], {"role": "assistant", "content": turn})
        calls = [ToolCall(f"call_{next(_ids)}", n, a) for n, a in turn]
        return AssistantTurn("", calls, {"role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}} for c in calls]})


@pytest.fixture()
def llm(monkeypatch):
    holder = {}

    def use(*turns, **kw):
        holder["llm"] = ScriptedLLM(*turns, **kw)
        monkeypatch.setattr(engine, "get_provider", lambda: holder["llm"])
        return holder["llm"]
    return use


def respond(msg, outcome="resolved"):
    return [("respond_to_customer", {"message": msg, "outcome": outcome})]


def web(c, msg, email, name="Customer"):
    return c.post("/api/channels/web-form", json={"name": name, "email": email, "message": msg}).json()["ref"]


def ticket(c, h, ref):
    tid = c.get(f"/api/tickets?q={ref}", headers=h).json()["items"][0]["id"]
    return c.get(f"/api/tickets/{tid}", headers=h).json()


def chat(c, name, email):
    s = c.post("/api/chat/sessions", json={"name": name, "email": email}).json()
    return s["session_id"], {"X-Chat-Token": s["token"]}


def events(t, name):
    return [e for e in t["events"] if e["event"] == name]


# ------------------------------------------------------------------------------------------
def test_order_agent_looks_up_order_and_answers(client, sup, llm):
    model = llm([("find_orders", {"order_id": "BL100045821"})],
                respond("Hi Sarah, your Aero fan is with Australia Post, tracking 33LPK1290045, due tomorrow."))
    ref = web(client, "hey where's my fan? order BL100045821", "sarah.mitchell@example.com", "Sarah Mitchell")
    t = ticket(client, sup, ref)
    assert model.tools_seen[0][:3] == ["get_customer_profile", "find_orders", "search_policy"]  # order_agent's kit
    assert t["status"] == "auto_resolved"
    reply = [m for m in t["messages"] if m["direction"] == "outbound"][-1]
    assert reply["author_name"] == "Beacon AI (Order agent)" and "33LPK1290045" in reply["body"]
    assert [e["data"]["tool"] for e in events(t, "agent_tool")] == ["find_orders"]
    assert any(m["direction"] == "internal" and "find_orders" in m["body"] for m in t["messages"])


def test_guardrail_blocks_invented_facts_then_accepts_fix(client, sup, llm):
    llm(respond("Your order BL100099999 shipped yesterday, tracking ZZ99999999AU."),
        [("find_orders", {"order_id": "BL100045821"})],
        respond("Your order BL100045821 is on its way with Australia Post."))
    ref = web(client, "where is my order BL100045821", "sarah.mitchell@example.com")
    t = ticket(client, sup, ref)
    blocked = events(t, "guardrail_blocked")
    assert blocked and "BL100099999" in str(blocked[0]["data"]["problems"])
    assert "BL100099999" not in str([m["body"] for m in t["messages"] if m["direction"] == "outbound"])
    assert t["status"] == "auto_resolved"


def test_order_privacy_enforced_in_tool(client, sup, llm):
    model = llm([("find_orders", {"order_id": "BL100045821"})],
                respond("I can't share details of that order from this email address.", "needs_info"))
    ref = web(client, "status of BL100045821 please", "stranger@example.com")
    t = ticket(client, sup, ref)
    tool_msgs = [m for m in model.messages_seen[1] if m["role"] == "tool"]
    assert "not linked to this customer" in tool_msgs[0]["content"] and "33LPK" not in tool_msgs[0]["content"]
    assert t["status"] == "awaiting_customer"


def test_refund_submitted_never_promised(client, sup, llm):
    llm([("find_orders", {"order_id": "BL100045822"}),
         ("create_refund_request", {"order_id": "BL100045822", "amount": 258, "reason": "charged twice"})],
        respond("Good news, your refund of $258.00 has been approved!", "pending_review"),
        respond("I've submitted a refund request for $258.00 for review; our team will confirm within one "
                "business day.", "pending_review"))
    ref = web(client, "I was charged twice for order BL100045822, $258", "james.oconnor@example.com", "James")
    t = ticket(client, sup, ref)
    assert events(t, "guardrail_blocked")
    assert t["status"] == "awaiting_approval"
    a = t["approvals"][0]
    assert a["action_type"] == "refund" and a["amount"] == 258 and a["status"] == "pending"
    assert "[Returns & refunds agent]" in a["ai_recommendation"]
    assert "has been approved" not in t["messages"][-1]["body"] or t["messages"][-1]["direction"] == "internal"


def test_refund_amount_cannot_exceed_order_total(client, sup, llm):
    model = llm([("create_refund_request", {"order_id": "BL100045822", "amount": 9999, "reason": "x"})],
                respond("Could you confirm the amount you were charged?", "needs_info"))
    web(client, "refund BL100045822 please, it was $9999", "james.oconnor@example.com")
    assert "must be between" in [m for m in model.messages_seen[1] if m["role"] == "tool"][0]["content"]


def test_chat_warranty_conversation_with_memory(client, sup, llm):
    """Turn 1: agent asks for the order number. Turn 2: same ticket, agent remembers turn 1,
    checks warranty and submits a replacement for approval."""
    sid, hdr = chat(client, "Priya Raman", "priya.raman@example.com")
    llm(respond("Sorry to hear that, Priya. What's your order number? It starts with BL.", "needs_info"))
    d = client.post(f"/api/chat/{sid}/messages", headers=hdr, json={"message": "my ceiling fan stopped working"}).json()
    assert d["status"] == "awaiting_customer" and "order number" in d["messages"][-1]["body"]

    model = llm([("check_warranty", {"order_id": "BL100045826"})],
                [("create_warranty_claim", {"order_id": "BL100045826", "fault_description": "motor won't spin"})],
                respond("Thanks Priya. Your Aero fan is covered by the 5-year DC motor warranty, so I've submitted a "
                        "replacement claim for review. Could you send a short video of the fault?", "pending_review"))
    d = client.post(f"/api/chat/{sid}/messages", headers=hdr, json={"message": "it's BL100045826"}).json()
    history = [m["content"] for m in model.messages_seen[0] if m["role"] in ("user", "assistant")]
    assert any("stopped working" in h for h in history) and any("order number" in h for h in history)
    assert "replacement claim" in d["messages"][-1]["body"] and d["status"] == "awaiting_approval"
    t = ticket(client, sup, d["ticket_ref"])
    assert t["approvals"][0]["action_type"] == "replacement" and t["approvals"][0]["payload"]["sku"] == "BL-FAN-52W"


def test_chat_never_shows_tracking_but_emails_it(client, sup, llm):
    sid, hdr = chat(client, "Sarah Mitchell", "sarah.mitchell@example.com")
    model = llm([("find_orders", {"order_id": "BL100045821"})],
                respond("Your Aero fan has shipped with Australia Post and is due tomorrow. I've emailed the "
                        "tracking link to s***@example.com."))
    d = client.post(f"/api/chat/{sid}/messages", headers=hdr, json={"message": "where's BL100045821?"}).json()
    tool_result = [m for m in model.messages_seen[1] if m["role"] == "tool"][0]["content"]
    assert "33LPK1290045" not in tool_result  # the model never even sees it in chat
    assert all("33LPK1290045" not in m["body"] for m in d["messages"])
    t = ticket(client, sup, d["ticket_ref"])
    assert any(m["channel"] == "email" and "33LPK1290045" in m["body"] for m in t["messages"])


def test_agent_detects_safety_the_keywords_missed(client, sup, llm):
    llm([("escalate_safety", {"description": "casing discoloured and odd smell"})])
    ref = web(client, "the fan housing has gone brown near the light and there's an odd smell", "tom.nguyen@example.com")
    t = ticket(client, sup, ref)
    assert t["safety_flag"] and t["priority"] == "P1" and t["team"] == "safety"
    out = [m for m in t["messages"] if m["direction"] == "outbound"]
    assert len(out) == 1 and "circuit breaker" in out[0]["body"]  # fixed wording, not AI text
    assert t["status"] == "awaiting_agent" and t["assigned_agent"]


def test_specialist_transfer_changes_toolkit(client, sup, llm):
    model = llm([("transfer_to_specialist", {"specialist": "warranty_agent", "reason": "faulty after troubleshooting"})],
                [("check_warranty", {"order_id": "BL100045826"})],
                respond("Your fan is covered by the 5-year DC motor warranty. Could you send a short video of it?",
                        "needs_info"))
    ref = web(client, "My smart remote won't pair with the fan from BL100045826, tried resetting, still dead",
              "priya.raman@example.com")
    t = ticket(client, sup, ref)
    assert "create_warranty_claim" not in model.tools_seen[0] and "create_warranty_claim" in model.tools_seen[1]
    assert events(t, "agent_transfer")[0]["data"]["to"] == "Warranty agent"
    assert [m for m in t["messages"] if m["direction"] == "outbound"][-1]["author_name"] == "Beacon AI (Warranty agent)"


def test_handoff_routes_to_team_with_summary(client, sup, llm):
    llm([("handoff_to_human", {"team": "trade", "reason": "quote needed",
                               "summary": "Needs 200 x BL-DL-10W3 for Collins St by Thursday"})],
        respond("Thanks! I've passed this to our Trade Desk with the details; they'll come back with pricing.",
                "handed_off"))
    ref = web(client, "need a quote for 200 downlight packs", "accounts@brightspark.example")
    t = ticket(client, sup, ref)
    assert t["team"] == "trade" and t["status"] == "awaiting_agent" and t["assigned_agent"]
    assert any(m["direction"] == "internal" and "Collins St" in m["body"] for m in t["messages"])


def test_rate_limited_llm_falls_back_to_rules(client, sup, llm):
    llm(fail=True)
    ref = web(client, "Where is my order BL100045821?", "sarah.mitchell@example.com", "Sarah Mitchell")
    t = ticket(client, sup, ref)
    assert events(t, "agent_failed")
    assert t["status"] == "auto_resolved" and "33LPK1290045" in t["messages"][-1]["body"]  # template answer


def test_after_human_replies_ai_only_drafts(client, sup, llm):
    llm(respond("Could you tell me more about the issue?", "needs_info"))
    ref = web(client, "the dimmer makes my downlights buzz", "tom.nguyen@example.com")
    t = ticket(client, sup, ref)
    client.post(f"/api/tickets/{t['id']}/reply", headers=sup, json={"body": "Hi Tom, which dimmer?", "resolve": False})
    llm(respond("Thanks Tom, that dimmer isn't compatible; please ask an electrician to swap it."))
    client.post("/api/channels/email/inbound", headers={"X-Webhook-Secret": "test-secret"}, json={
        "from_email": "tom.nguyen@example.com", "subject": f"Re: [{ref}]", "text": "It's a Clipsal 32E"})
    t = ticket(client, sup, ref)
    assert t["messages"][-1]["direction"] == "inbound"  # nothing auto-sent over the human's head
    assert "dimmer isn't compatible" in t["drafts"][-1]["body"] and t["status"] == "awaiting_agent"


def test_wiring_instructions_blocked(client, sup, llm):
    llm(respond("Just connect the active wire to terminal L and the neutral to N."),
        respond("Swapping a dimmer is hardwired work, so please book a licensed electrician.", "resolved"))
    ref = web(client, "how do I install a new dimmer for my downlights?", "tom.nguyen@example.com")
    t = ticket(client, sup, ref)
    assert events(t, "guardrail_blocked") and "terminal L" not in str(t["messages"])


def test_agents_off_uses_classic_mode(client, sup, llm, settings):
    settings.agent_mode = False
    model = llm(respond("should not be used"))
    ref = web(client, "Where is my order BL100045821?", "sarah.mitchell@example.com")
    assert model.tools_seen == [] and ticket(client, sup, ref)["status"] == "auto_resolved"


def test_console_shows_agent_trace(client, llm):
    llm([("check_product_stock", {"query": "rattan pendant"})],
        respond("The Coastal Rattan Pendant is $149.00 and in stock at Richmond VIC."))
    ref = web(client, "do you have the rattan pendant?", "a@example.com")
    h = login(client)
    t = ticket(client, h, ref)
    assert t["status"] == "auto_resolved"
    assert events(t, "agent_start")[0]["data"]["specialist"] == "product_agent"
    assert events(t, "ai_reply")[0]["data"]["outcome"] == "resolved"
