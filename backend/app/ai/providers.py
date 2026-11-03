"""Provider-agnostic LLM access. Each provider exposes two calls:
  structured(system, user, schema, fast) -> dict   (forced JSON matching schema)
  text(system, user, fast) -> str
"""
import json
import logging
from abc import ABC, abstractmethod
from functools import lru_cache

from ..config import get_settings

log = logging.getLogger("beacon.ai")


class ProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    name = "abstract"

    @abstractmethod
    def structured(self, system: str, user: str, schema: dict, fast: bool = True) -> dict: ...

    @abstractmethod
    def text(self, system: str, user: str, fast: bool = False, max_tokens: int = 900) -> str: ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self):
        import anthropic
        s = get_settings()
        if not s.anthropic_api_key:
            raise ProviderError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key, timeout=s.ai_timeout_seconds, max_retries=2)
        self.model, self.fast_model = s.anthropic_model, s.anthropic_fast_model

    def structured(self, system, user, schema, fast=True):
        tool = {"name": "record_result", "description": "Record the structured result.", "input_schema": schema}
        try:
            resp = self.client.messages.create(
                model=self.fast_model if fast else self.model, max_tokens=1024, system=system,
                messages=[{"role": "user", "content": user}], tools=[tool],
                tool_choice={"type": "tool", "name": "record_result"})
        except Exception as e:  # network, auth, rate limit
            raise ProviderError(str(e)) from e
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input)
        raise ProviderError("no tool_use block in response")

    def text(self, system, user, fast=False, max_tokens=900):
        try:
            resp = self.client.messages.create(
                model=self.fast_model if fast else self.model, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": user}])
        except Exception as e:
            raise ProviderError(str(e)) from e
        return "".join(getattr(b, "text", "") for b in resp.content).strip()


class OpenAICompatibleProvider(LLMProvider):  # noqa: D101
    """OpenAI, and every free provider that speaks the same API (Groq, Google Gemini)."""

    def __init__(self, name: str, api_key: str | None, model: str, fast_model: str, base_url: str | None = None,
                 extra: dict | None = None):
        import openai
        if not api_key:
            raise ProviderError(f"{name.upper()}_API_KEY not set")
        s = get_settings()
        self.name = name
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=s.ai_timeout_seconds, max_retries=2)
        self.model, self.fast_model = model, fast_model
        self.extra = extra or {}

    def _chat(self, fast: bool, system: str, user: str, max_tokens: int, **kw) -> str:
        resp = self.client.chat.completions.create(
            model=self.fast_model if fast else self.model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            extra_body=self.extra or None, **kw)
        return (resp.choices[0].message.content or "").strip()

    @staticmethod
    def _parse(raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[-1]
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1])

    def structured(self, system, user, schema, fast=True):
        # Free providers differ in structured-output support, so try strict -> JSON mode -> plain prompt.
        prompt = system + "\n\nRespond with ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema)
        attempts = [
            dict(response_format={"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": False}}),
            dict(response_format={"type": "json_object"}),
            dict(),
        ]
        last: Exception | None = None
        for kw in attempts:
            try:
                return self._parse(self._chat(fast, prompt, user, 1500, **kw))
            except Exception as e:  # unsupported format, bad JSON, transient error -> next attempt
                last = e
                if "429" in str(e) or "rate" in str(e).lower():
                    break  # rate limited: don't burn more free quota, fall back to rules
        raise ProviderError(str(last))

    def text(self, system, user, fast=False, max_tokens=900):
        try:
            return self._chat(fast, system, user, max(max_tokens, 2000))
        except Exception as e:
            raise ProviderError(str(e)) from e


class OpenAIProvider(OpenAICompatibleProvider):
    def __init__(self):
        s = get_settings()
        super().__init__("openai", s.openai_api_key, s.openai_model, s.openai_fast_model)


@lru_cache
def get_provider() -> LLMProvider | None:
    """Returns the configured LLM, or None for rules-only mode (also when keys are missing)."""
    s = get_settings()
    try:
        if s.ai_provider == "anthropic":
            return AnthropicProvider()
        if s.ai_provider == "openai":
            return OpenAIProvider()
        if s.ai_provider == "groq":  # free tier, no card: https://console.groq.com/keys
            return OpenAICompatibleProvider("groq", s.groq_api_key, s.groq_model, s.groq_fast_model,
                                            "https://api.groq.com/openai/v1", {"reasoning_effort": "low"})
        if s.ai_provider == "gemini":  # free tier, no card: https://aistudio.google.com/apikey
            return OpenAICompatibleProvider("gemini", s.gemini_api_key, s.gemini_model, s.gemini_fast_model,
                                            "https://generativelanguage.googleapis.com/v1beta/openai/")
    except ProviderError as e:
        log.warning("AI provider '%s' unavailable (%s) - running in rules-only mode", s.ai_provider, e)
    return None
