"""Executes approved human-in-the-loop actions against the commerce platform.

In mock mode the action is recorded only. With ORDER_CONNECTOR=magento a refund calls
POST /V1/order/{entity_id}/refund (offline credit memo); wire the payment-gateway refund
and return-label provider (e.g. Australia Post MyPost Business) here during Month 3."""
import logging

from .config import get_settings
from .models import Approval

log = logging.getLogger("beacon.actions")


class ActionError(RuntimeError):
    pass


def execute(approval: Approval) -> dict:
    s = get_settings()
    if s.order_connector != "magento":
        log.info("[mock] executed %s for ticket %s amount=%s", approval.action_type, approval.ticket_id, approval.amount)
        return {"executed": True, "mode": "mock"}
    import httpx
    order_id = approval.payload.get("order_id")
    if approval.action_type == "refund" and order_id:
        h = {"Authorization": f"Bearer {s.magento_access_token}"}
        base = s.magento_base_url.rstrip("/") + "/rest/V1"
        found = httpx.get(f"{base}/orders", headers=h, timeout=15, params={
            "searchCriteria[filter_groups][0][filters][0][field]": "increment_id",
            "searchCriteria[filter_groups][0][filters][0][value]": order_id}).json().get("items", [])
        if not found:
            raise ActionError(f"order {order_id} not found in Magento")
        r = httpx.post(f"{base}/order/{found[0]['entity_id']}/refund", headers=h, timeout=15,
                       json={"notify": True, "arguments": {"adjustment_positive": approval.amount or 0,
                                                            "shipping_amount": 0}, "items": []})
        if r.status_code >= 400:
            raise ActionError(f"Magento refund failed: {r.text[:300]}")
        return {"executed": True, "mode": "magento", "creditmemo_id": r.json()}
    if approval.action_type == "refund":
        raise ActionError("refund has no order number - process it manually in Magento, then resolve the ticket")
    step = {"return": "email the customer a prepaid return label (MyPost Business) and book the refund on receipt",
            "replacement": "raise a replacement order / warehouse dispatch for "
                           f"{approval.payload.get('sku', 'the item')}"}.get(approval.action_type, approval.action_type)
    return {"executed": False, "mode": "magento", "manual_required": step}
