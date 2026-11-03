"""Classification + drafting. AI output is always merged with the deterministic layer:
regex IDs win, safety flags are a union, and any provider failure degrades to rules."""
import json
import logging

from . import rules
from .providers import ProviderError, get_provider
from .taxonomy import CLASSIFICATION_SCHEMA, INTENTS, SENTIMENTS

log = logging.getLogger("beacon.ai")

CLASSIFY_SYSTEM = """You are the triage engine for Beacon Lighting's customer service (Australian lighting and ceiling-fan retailer).
Classify the customer's message into exactly one intent:
{intents}

Rules:
- Extract identifiers exactly as written. Beacon order numbers look like BL followed by 9 digits.
- safety_concern = true for ANY sign of electrical hazard (sparks, smoke, burning smell, melting, heat, shock), injury, a fitting falling, product recall, or legal/regulator threats. When unsure, set true.
- urgency: 0-30 routine, 30-60 time-sensitive or unhappy, 60-80 very upset / business impact, 80-100 safety.
- confidence reflects how clearly the message fits ONE intent; lower it for ambiguous or multi-issue messages.
Return your answer by calling the record_result tool."""

DRAFT_SYSTEM = """You write customer service replies for Beacon Lighting, an Australian lighting and ceiling-fan retailer.
Voice: warm, clear, concise, Australian English. Address the customer by first name if known. 120 words or fewer unless steps are needed.

Hard rules:
1. Only state facts that appear in the CONTEXT block (orders, stock, policy). Never invent dates, prices, stock, tracking numbers, or policy.
2. Never promise a refund, replacement, or credit. Say a team member will review and confirm.
3. Never give wiring or electrical installation instructions; hardwired work must be done by a licensed electrician.
4. If information is missing (e.g. no order number), ask for exactly what you need.
5. Sign off as "The Beacon Lighting Team". Output only the reply body, no subject line."""


def _merge(ai: dict, text: str, segment: str) -> dict:
    base = rules.classify(text, segment)
    out = dict(ai)
    if out.get("intent") not in INTENTS:
        out["intent"] = base["intent"]
    if out.get("sentiment") not in SENTIMENTS:
        out["sentiment"] = base["sentiment"]
    try:
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
        out["urgency"] = max(0, min(100, int(out.get("urgency", base["urgency"]))))
    except (TypeError, ValueError):
        out["confidence"], out["urgency"] = 0.5, base["urgency"]
    raw = out.get("entities")
    ents = {k: v for k, v in (raw if isinstance(raw, dict) else {}).items() if v not in (None, "", [])}
    ents.update(base["entities"])  # regex-extracted IDs are authoritative
    out["entities"] = ents
    ai_reasons = out.get("safety_reasons")
    reasons = set(r for r in (ai_reasons if isinstance(ai_reasons, list) else []) if isinstance(r, str)) | set(base["safety_reasons"])
    out["safety_concern"] = bool(out.get("safety_concern")) or bool(base["safety_reasons"])
    if out["safety_concern"] and not reasons:
        reasons = {"ai_flagged"}
    out["safety_reasons"] = sorted(reasons)
    if out["safety_concern"]:
        out["urgency"] = max(out["urgency"], 85)
    out.setdefault("summary", base["summary"])
    out.setdefault("reasoning", "")
    return out


def classify(text: str, customer: dict | None = None) -> tuple[dict, str]:
    segment = (customer or {}).get("segment", "retail")
    provider = get_provider()
    if provider:
        intents = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
        ctx = f"Customer segment: {segment}" + (f" ({customer.get('trade_tier')} trade)" if customer and customer.get("trade_tier") else "")
        try:
            ai = provider.structured(CLASSIFY_SYSTEM.format(intents=intents),
                                     f"{ctx}\n\nCustomer message:\n<<<\n{text}\n>>>", CLASSIFICATION_SCHEMA, fast=True)
            if not isinstance(ai, dict):
                raise ProviderError(f"unexpected classification payload: {type(ai).__name__}")
            return _merge(ai, text, segment), provider.name
        except Exception as e:  # provider error OR malformed output: degrade to rules, never fail the ticket
            log.warning("classification fell back to rules: %s", e)
            return rules.classify(text, segment), f"rules (fallback from {provider.name})"
    return rules.classify(text, segment), "rules"


def draft(message: str, classification: dict, context: dict, fallback: str) -> tuple[str, str]:
    """Agent-facing draft. `fallback` is the grounded template reply used when no LLM is available."""
    provider = get_provider()
    if not provider:
        return fallback, "rules"
    user = (f"CONTEXT (the only facts you may use):\n{json.dumps(context, indent=1, default=str)}\n\n"
            f"Triage: intent={classification['intent']}, sentiment={classification['sentiment']}\n\n"
            f"Customer message:\n<<<\n{message}\n>>>\n\nWrite the reply.")
    try:
        out = provider.text(DRAFT_SYSTEM, user)
        return (out or fallback), provider.name
    except Exception as e:
        log.warning("drafting fell back to template: %s", e)
        return fallback, f"rules (fallback from {provider.name})"
