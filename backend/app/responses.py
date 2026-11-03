"""Grounded reply templates. Everything that is SENT without a human uses these:
every fact in the message comes straight from a system of record, so auto-replies
cannot hallucinate. LLM output is only ever shown to agents as a draft."""
from datetime import datetime

from .connectors.base import Order, StockInfo

SIGN_OFF = "\n\nKind regards,\nThe Beacon Lighting Team"


def first_name(name: str | None) -> str:
    if not name:
        return "there"
    word = name.strip().split()[0]
    return "there" if any(x in name.lower() for x in ("pty", "ltd", "builders", "electrical")) else word


def _d(dt: datetime | None) -> str:
    return dt.strftime("%A %-d %B") if dt else "soon"


def _items(order: Order) -> str:
    return ", ".join(f"{l.qty} x {l.name}" for l in order.lines)


def order_status(order: Order, name: str | None) -> str:
    hi = f"Hi {first_name(name)},\n\n"
    ref = f"Your order {order.order_id} ({_items(order)})"
    if order.status == "shipped":
        body = (f"{ref} is on its way with {order.carrier}. Tracking number: {order.tracking_number}.\n"
                f"Track it here: {order.tracking_url}\n\nEstimated delivery: {_d(order.eta)}.")
    elif order.status == "processing":
        body = (f"{ref} is being prepared at our warehouse and hasn't shipped yet. "
                f"We expect to dispatch it by {_d(order.eta)}, and you'll receive a tracking link by email as soon as it leaves.")
    elif order.status == "ready_for_pickup":
        body = (f"Good news - {ref} is ready to collect from Beacon Lighting {order.pickup_store}. "
                "Please bring your order confirmation and photo ID. We'll hold it for 7 days.")
    elif order.status == "delivered":
        body = (f"{ref} was delivered on {_d(order.delivered_at)} by {order.carrier}. If you can't find it, "
                "just reply to this message and we'll open a trace with the carrier straight away.")
    elif order.status in ("cancelled", "refunded"):
        body = f"{ref} shows as {order.status}. If this isn't what you expected, reply and a team member will look into it."
    else:
        body = f"{ref} currently has the status '{order.status}'."
    return hi + body + "\n\nAnything else we can help with? Just reply here." + SIGN_OFF


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "the email address on your order"
    user, domain = email.split("@", 1)
    return f"{user[0]}***@{domain}"


def order_status_by_email(name: str | None, email: str | None) -> str:
    return (f"Hi {first_name(name)},\n\nI've found your order. To keep your details private, I've just emailed the "
            f"latest status and tracking to {mask_email(email)}.\n\nAnything else I can help with?")


def ask_order_number(name: str | None) -> str:
    return (f"Hi {first_name(name)},\n\nHappy to check on that for you. Could you reply with your order number? "
            "It starts with BL and is in your order confirmation email (for example BL100012345).\n\n"
            "As soon as we have it we'll send you the latest status." + SIGN_OFF)


def availability(stocks: list[StockInfo], name: str | None) -> str:
    parts = []
    for s in stocks:
        in_store = [st for st in s.stores if st.qty > 0]
        line = f"- {s.name} (${s.price:,.2f})\n"
        if s.online_qty > 0:
            line += "  Online: in stock, available for delivery\n"
        else:
            line += "  Online: currently out of stock" + (f", expected back around {_d(s.restock_eta)}" if s.restock_eta else "") + "\n"
        if in_store:
            line += "  In store: " + ", ".join(f"{st.store} ({st.qty})" for st in in_store[:4]) + "\n"
        else:
            line += "  In store: not available in any store right now\n"
        parts.append(line)
    return (f"Hi {first_name(name)},\n\nHere's the live availability:\n\n" + "\n".join(parts) +
            "\nStock levels change quickly, so for in-store pickup we recommend ordering online with Click and Collect "
            "to reserve it." + SIGN_OFF)


def policy(passages: list, name: str | None) -> str:
    body = "\n\n".join(f"{p.title}: {p.text}" for p in passages[:2])
    return f"Hi {first_name(name)},\n\nThanks for getting in touch.\n\n{body}\n\nIf that doesn't answer your question, just reply and one of our team will help." + SIGN_OFF


def safety_ack(name: str | None, ref: str) -> str:
    return (f"Hi {first_name(name)},\n\nThank you for letting us know - your safety comes first.\n\n"
            "Please stop using the product now: switch it off at the wall and at the circuit breaker (switchboard), "
            "and do not touch the fitting. If there is any smoke or fire, leave the area and call 000.\n\n"
            f"Your report ({ref}) has been escalated to our Safety team as a priority and a team member will contact you shortly."
            + SIGN_OFF)


def acknowledgement(name: str | None, ref: str, team: str, hours: float) -> str:
    window = f"{int(hours * 60)} minutes" if hours < 1 else f"{hours:g} hour" + ("s" if hours != 1 else "")
    return (f"Hi {first_name(name)},\n\nThanks for contacting Beacon Lighting - we've received your message "
            f"(reference {ref}) and passed it to our {team}. You can expect a reply within {window}." + SIGN_OFF)


def warranty_term(product_name: str) -> tuple[int, str]:
    n = product_name.lower()
    if "dc ceiling fan" in n:
        return 60, "5-year DC motor warranty (12 months on parts)"
    if "ac ceiling fan" in n:
        return 36, "3-year AC motor warranty (12 months on parts)"
    if "downlight" in n or "track" in n or "pendant" in n or "wall light" in n:
        return 36, "3-year LED fitting warranty"
    if "smart" in n or "strip" in n:
        return 24, "2-year warranty"
    return 12, "12-month standard warranty"


# ---- agent drafts used when no LLM is configured -----------------------------------------

def draft_return(name, order: Order | None, eligible: bool | None) -> str:
    hi = f"Hi {first_name(name)},\n\n"
    if not order:
        return hi + ("Sorry the item didn't work out. So we can arrange your return, could you send your order number "
                     "(it starts with BL) and the item you'd like to return?" + SIGN_OFF)
    if eligible:
        return hi + (f"Thanks for reaching out about order {order.order_id}. Your purchase is within our 30-day change-of-mind "
                     "window, so you can return unused items in their original packaging to any Beacon Lighting store, or we "
                     "can email you a prepaid return label. Once the item is received and checked, a team member will confirm "
                     "your refund." + SIGN_OFF)
    return hi + (f"Thanks for reaching out about order {order.order_id}. This order is outside our 30-day change-of-mind "
                 "return window. If the item is faulty, we can help through a warranty claim - just let us know what's "
                 "wrong with it." + SIGN_OFF)


def draft_refund(name, order: Order | None) -> str:
    ref = f" for order {order.order_id}" if order else ""
    return (f"Hi {first_name(name)},\n\nThanks for your message about a refund{ref}. A member of our team is reviewing "
            "this now and will confirm the outcome with you. Approved refunds are processed within 2 business days, and "
            "your bank may take a further 3-5 business days to show the funds." + SIGN_OFF)


def draft_warranty(name, order: Order | None, in_warranty: bool | None, term: str | None) -> str:
    hi = f"Hi {first_name(name)},\n\nI'm sorry to hear your product isn't working. "
    if not order:
        return hi + ("To start a warranty claim, please reply with your order number or receipt, a short description of the "
                     "fault, and a photo or video. For hardwired fittings, please include your electrician's certificate of "
                     "compliance." + SIGN_OFF)
    status = (f"Your purchase from order {order.order_id} is covered by our {term}."
              if in_warranty else f"Order {order.order_id} appears to be outside the {term}, but our team will review it.")
    return hi + status + (" Could you send a photo or short video of the fault and, for hardwired fittings, your "
                          "electrician's certificate of compliance? Our Warranty team will then confirm a repair or "
                          "replacement." + SIGN_OFF)


def draft_from_kb(name, passages: list) -> str:
    if not passages:
        return f"Hi {first_name(name)},\n\nThanks for getting in touch. [Agent: add answer]" + SIGN_OFF
    return policy(passages, name)


def draft_trade(name, customer) -> str:
    mgr = f" Your account manager, {customer.account_manager}, has been notified." if customer and customer.account_manager else ""
    return (f"Hi {first_name(name)},\n\nThanks for your enquiry - our Trade Desk is on it.{mgr} We'll come back to you "
            "with pricing, stock reservations and delivery options, usually within one business day." + SIGN_OFF)


def draft_safety(name, ref: str) -> str:
    return (f"Hi {first_name(name)},\n\nI'm following up on your safety report ({ref}). Please keep the product switched off "
            "at the circuit breaker until it has been checked by a licensed electrician.\n\nOur Safety team is reviewing your report "
            "and will confirm the next steps with you. Could you send a photo of the product and the "
            "area around it, and let us know a good time to call you today?" + SIGN_OFF)


def draft_generic(name) -> str:
    return f"Hi {first_name(name)},\n\nThanks for getting in touch with Beacon Lighting. [Agent: add answer]" + SIGN_OFF
