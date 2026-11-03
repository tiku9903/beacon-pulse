"""Deterministic layer. Runs on every ticket, whatever AI provider is configured:
- entity extraction (order numbers, SKUs, money) — regex beats LLMs on exact IDs
- safety keyword scan — the AI can ADD safety flags but never remove these
- a complete keyword classifier + template drafts, used when no LLM is configured or it fails
"""
import re

SAFETY_PATTERNS: dict[str, list[str]] = {
    "electrical_hazard": [r"\bspark", r"\bsmok(e|ing)\b", r"burn(ing|t)?\s+smell", r"smell(s|ed)?\s+(of\s+)?burn",
                          r"\bmelt(ed|ing)\b", r"\bscorch", r"exposed\s+wir", r"\bhot to (the )?touch",
                          r"\bfire\b", r"\bcaught fire", r"\bflames?\b", r"\barc(ing)?\b", r"\bbuzz(ing)? loud"],
    "electric_shock": [r"\bshock(ed)?\b", r"\belectrocut", r"\bzapped\b", r"\btingl"],
    "injury_or_falling": [r"\binjur", r"\bhurt\b", r"\bhospital", r"\bfell (off|from|down)", r"\bfallen\b",
                          r"came (off|down) (the )?ceiling", r"\bblade (flew|came off)", r"\bcut (my|his|her)"],
    "recall_or_regulator": [r"\brecall", r"\baccc\b", r"fair trading", r"product safety"],
    "legal_threat": [r"\blawyer", r"\blegal action", r"\bsue\b", r"\bsuing\b", r"\bsolicitor"],
}

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "order_status": [r"where('?s| is) my (order|fan|parcel|delivery|light|package|stuff)", r"where my \w+ is", r"\btrack", r"\bshipped\b", r"\bdeliver(y|ed)?\b", r"\barriv",
                     r"order status", r"\bdispatch", r"click and collect", r"ready (for|to) (pick|collect)",
                     r"hasn'?t (come|arrived)", r"\bparcel"],
    "product_availability": [r"in stock", r"\bstock\b", r"\bavailab", r"back in stock", r"do you (have|sell|carry)",
                             r"\bsold out", r"how much (is|are)", r"\bprice of"],
    "store_policy": [r"(opening|trading) hours", r"what time", r"\bopen (on|until|till)", r"price match",
                     r"return policy", r"how long (does|do|is) (the )?warranty", r"warranty (period|policy)",
                     r"delivery (times?|cost|fee)", r"do you deliver"],
    "return_request": [r"\breturn", r"\bexchange", r"change of mind", r"wrong (item|colou?r|size)",
                       r"doesn'?t (fit|match)", r"send (it )?back"],
    "refund_billing": [r"\brefund", r"charged twice", r"double charg", r"\binvoice", r"\bpayment", r"\bbilling",
                       r"\bmoney back", r"overcharg"],
    "warranty_claim": [r"stopped working", r"\bfaulty", r"\bbroken", r"\bwarranty claim", r"\bdefect",
                       r"not working", r"\bdead\b", r"won'?t turn on", r"\bdied\b", r"under warranty"],
    "technical_support": [r"\bflicker", r"\bwobbl", r"\bpair(ing)?\b", r"\bwi-?fi", r"\bapp\b", r"\bdimmer",
                          r"\bremote\b", r"how (do|to) i", r"\bsetup\b", r"set up", r"\binstall", r"\bnois(y|e)",
                          r"won'?t connect", r"\breset"],
    "trade_inquiry": [r"\btrade account", r"\bbulk\b", r"\bquote\b", r"\bproject\b", r"trade pric", r"\bsite\b",
                      r"\bbuilder", r"\belectrician", r"\bcommercial", r"\bfit-?out", r"\bunits\b"],
}

_NEG = [r"\bdisappoint", r"\bfrustrat", r"\bunhappy", r"\bannoy", r"\bpoor\b", r"\bworst", r"\bstill (no|not)",
        r"\bunacceptable", r"\bridiculous", r"\bwaste", r"\bterrible", r"\bnot good enough", r"\bagain\b"]
_ANGRY = [r"\bfurious", r"\bdisgust", r"\bnever (shop|buy|again)", r"\bjoke\b", r"\bscam", r"!!", r"\bfed up",
          r"\bsick of", r"\bappalling", r"\babsolutely"]
_POS = [r"\bthank", r"\bgreat\b", r"\blove\b", r"\bawesome", r"\bexcellent", r"\bhappy\b"]

ORDER_RE = re.compile(r"\b(BL[\s-]?\d{9})\b|\border\s*(?:no\.?|number|#)?\s*[:#]?\s*(\d{9})\b", re.I)
SKU_RE = re.compile(r"\b(BL-[A-Z]{2,4}-[A-Z0-9]{2,4})\b", re.I)
AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _hits(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


def safety_scan(text: str) -> list[str]:
    return [cat for cat, pats in SAFETY_PATTERNS.items() if _hits(pats, text)]


def extract_entities(text: str) -> dict:
    ents: dict = {}
    m = ORDER_RE.search(text)
    if m:
        raw = m.group(1) or ("BL" + m.group(2))
        ents["order_id"] = re.sub(r"[\s-]", "", raw).upper()
    m = SKU_RE.search(text)
    if m:
        ents["sku"] = m.group(1).upper()
    m = AMOUNT_RE.search(text)
    if m:
        ents["amount"] = float(m.group(1).replace(",", ""))
    return ents


def sentiment(text: str) -> str:
    if _hits(_ANGRY, text) >= 1 and _hits(_NEG, text) >= 1 or _hits(_ANGRY, text) >= 2:
        return "angry"
    if _hits(_NEG, text):
        return "negative"
    if _hits(_POS, text):
        return "positive"
    return "neutral"


def _product_query(text: str) -> str | None:
    m = re.search(r"(?:the|your|a|an)\s+((?:[\w\"'-]+\s){0,5}(?:fan|downlight|pendant|bulb|light|strip|lamp|kit|globe)s?)",
                  text, re.I)
    if not m:
        return None
    q = m.group(1).strip()
    for sep in (" my ", " the ", " from ", " a "):  # keep only the product phrase itself
        if sep in f" {q} ":
            q = f" {q} ".split(sep)[-1].strip()
    return q or None


def classify(text: str, segment: str = "retail") -> dict:
    scores = {intent: _hits(pats, text) for intent, pats in _INTENT_KEYWORDS.items()}
    ents = extract_entities(text)
    # an order number makes "where is it" far more likely; "fault" words beat "return" words
    if ents.get("order_id"):
        scores["order_status"] += 0.5
    if scores["store_policy"]:  # policy phrasing ("return policy", "warranty period") is more specific
        scores["store_policy"] += 0.5
    if segment == "trade":
        scores["trade_inquiry"] += 0.5
    if scores["warranty_claim"] and scores["technical_support"]:
        scores["warranty_claim"] += 0.5 if re.search(r"stopped|faulty|broken|dead|died", text, re.I) else 0
    best = max(scores, key=scores.get)
    top = scores[best]
    if top == 0:
        best, conf = "other", 0.4
    else:
        second = sorted(scores.values())[-2]
        if second == 0:  # unambiguous: only one intent matched
            conf = min(0.95, 0.82 + 0.05 * (top - 1))
        else:
            conf = max(0.5, min(0.9, 0.6 + 0.1 * (top - second)))
    pq = _product_query(text)
    if pq:
        ents["product_query"] = pq
    reasons = safety_scan(text)
    sent = sentiment(text)
    urgency = 20 + (25 if sent == "negative" else 45 if sent == "angry" else 0) + (60 if reasons else 0)
    return {
        "intent": best, "confidence": round(conf, 2), "entities": ents, "sentiment": sent,
        "urgency": min(urgency, 100), "safety_concern": bool(reasons), "safety_reasons": reasons,
        "summary": text.strip().split("\n")[0][:160],
        "reasoning": ("Rules engine matched: " + ", ".join(k.replace("_", " ") for k, v in
                      sorted(scores.items(), key=lambda x: -x[1]) if v)) if top else "Rules engine: no intent keywords matched",
    }
