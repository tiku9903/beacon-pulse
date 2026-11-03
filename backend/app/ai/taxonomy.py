"""The 8 resolution paths from the roadmap ("All 8 Paths Live") and the teams they route to."""

INTENTS: dict[str, str] = {
    "order_status": "Where is my order / delivery / tracking / click-and-collect readiness for an existing order.",
    "product_availability": "Is a product in stock, at which store, price, when will it be back in stock.",
    "store_policy": "General questions answered by policy: trading hours, delivery times, price match, how returns work in general, warranty terms in general.",
    "return_request": "Customer wants to return or exchange a specific item they bought (change of mind, wrong item).",
    "refund_billing": "Refund status, refund request, double charge, payment or invoice problem.",
    "warranty_claim": "A product they own is faulty, broken, stopped working and they want repair/replacement under warranty.",
    "technical_support": "How-to or troubleshooting: smart bulb pairing, flickering, fan wobble, remote, dimmer compatibility.",
    "trade_inquiry": "Trade account applications, bulk/project quotes, trade pricing, site deliveries.",
    "other": "Anything else (feedback, compliments, careers, spam).",
}

TEAMS: dict[str, str] = {
    "customer_support": "Customer Support",
    "returns": "Returns Management",
    "warranty": "Warranty Team",
    "technical": "Technical Support",
    "trade": "Trade Desk",
    "safety": "Safety & Escalations",
}

INTENT_TEAM = {
    "order_status": "customer_support",
    "product_availability": "customer_support",
    "store_policy": "customer_support",
    "return_request": "returns",
    "refund_billing": "returns",
    "warranty_claim": "warranty",
    "technical_support": "technical",
    "trade_inquiry": "trade",
    "other": "customer_support",
}

SENTIMENTS = ["positive", "neutral", "negative", "angry"]

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "confidence": {"type": "number", "description": "0-1 confidence in the intent"},
        "entities": {
            "type": "object",
            "properties": {
                "order_id": {"type": ["string", "null"], "description": "Beacon order number, e.g. BL100045821"},
                "sku": {"type": ["string", "null"]},
                "product_query": {"type": ["string", "null"], "description": "Product named by the customer"},
                "amount": {"type": ["number", "null"], "description": "Money amount mentioned, AUD"},
                "store": {"type": ["string", "null"]},
                "issue_description": {"type": ["string", "null"], "description": "Fault or problem in a few words"},
            },
        },
        "sentiment": {"type": "string", "enum": SENTIMENTS},
        "urgency": {"type": "integer", "description": "0-100; 80+ = safety risk, site down, or customer threatening to leave"},
        "safety_concern": {"type": "boolean", "description": "Any electrical hazard, injury, fire, smoke, shock, recall, or legal/regulator threat"},
        "safety_reasons": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string", "description": "One-sentence summary for the agent"},
        "reasoning": {"type": "string", "description": "Brief justification of the intent"},
    },
    "required": ["intent", "confidence", "entities", "sentiment", "urgency", "safety_concern", "summary"],
}
