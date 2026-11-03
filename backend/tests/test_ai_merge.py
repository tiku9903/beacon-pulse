"""LLM output is merged with the deterministic layer: IDs and safety flags cannot be lost,
and provider failures degrade to rules."""
from app.ai import engine
from app.ai.providers import ProviderError


class FakeLLM:
    name = "fake"

    def __init__(self, result=None, fail=False):
        self.result, self.fail = result, fail

    def structured(self, *a, **k):
        if self.fail:
            raise ProviderError("down")
        return self.result

    def text(self, *a, **k):
        if self.fail:
            raise ProviderError("down")
        return "LLM draft"


def test_llm_cannot_clear_safety_or_drop_ids(monkeypatch):
    llm = FakeLLM({"intent": "warranty_claim", "confidence": 0.93, "entities": {"order_id": None},
                   "sentiment": "negative", "urgency": 40, "safety_concern": False, "summary": "fan broke"})
    monkeypatch.setattr(engine, "get_provider", lambda: llm)
    c, provider = engine.classify("Fan BL100045826 stopped and smells of burning")
    assert provider == "fake" and c["intent"] == "warranty_claim"
    assert c["entities"]["order_id"] == "BL100045826"
    assert c["safety_concern"] is True and c["urgency"] >= 85


def test_invalid_llm_values_are_sanitised(monkeypatch):
    llm = FakeLLM({"intent": "made_up", "confidence": 7, "entities": {}, "sentiment": "meh", "urgency": 500,
                   "safety_concern": False, "summary": ""})
    monkeypatch.setattr(engine, "get_provider", lambda: llm)
    c, _ = engine.classify("where is my order?")
    assert c["intent"] == "order_status" and c["confidence"] == 1.0 and c["urgency"] == 100


def test_provider_outage_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(engine, "get_provider", lambda: FakeLLM(fail=True))
    c, provider = engine.classify("where is my order BL100045821")
    assert provider.startswith("rules (fallback") and c["intent"] == "order_status"
    text, p = engine.draft("hi", c, {}, "TEMPLATE")
    assert text == "TEMPLATE"


def test_malformed_llm_output_falls_back(monkeypatch):
    llm = FakeLLM({"intent": "order_status", "entities": ["not", "a", "dict"], "safety_reasons": "x",
                   "confidence": 0.9, "sentiment": "neutral", "urgency": 10, "safety_concern": False})
    monkeypatch.setattr(engine, "get_provider", lambda: llm)
    c, provider = engine.classify("where is my order BL100045821")
    assert c["entities"]["order_id"] == "BL100045821"
    monkeypatch.setattr(engine, "get_provider", lambda: FakeLLM(["garbage"]))
    c, provider = engine.classify("where is my order")
    assert provider.startswith("rules (fallback")


def test_free_provider_structured_output_fallbacks():
    """Groq/Gemini: if strict JSON-schema mode is rejected, JSON mode / plain prompt is tried next."""
    from types import SimpleNamespace

    from app.ai.providers import OpenAICompatibleProvider

    calls = []

    class FakeCompletions:
        def create(self, **kw):
            calls.append(kw.get("response_format", {}).get("type"))
            if kw.get("response_format", {}).get("type") == "json_schema":
                raise RuntimeError("400 json_schema not supported for this model")
            content = '```json\n{"intent": "order_status", "confidence": 0.9}\n```'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    p = OpenAICompatibleProvider("groq", "k", "m", "fm", "https://api.groq.com/openai/v1")
    p.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    assert p.structured("sys", "user", {"type": "object"}) == {"intent": "order_status", "confidence": 0.9}
    assert calls == ["json_schema", "json_object"]
