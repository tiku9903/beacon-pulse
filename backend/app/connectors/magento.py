"""Production adapters for Magento 2 (Adobe Commerce) REST and a generic REST CRM.

Magento: create an Integration in Admin > System > Extensions > Integrations with read
access to Sales > Operations > Orders/Shipments and Catalog > Inventory, then set
MAGENTO_BASE_URL (e.g. https://www.beaconlighting.com.au) and MAGENTO_ACCESS_TOKEN.
"""
from datetime import datetime

import httpx

from .base import (CRMConnector, CustomerRecord, InventoryConnector, Order, OrderConnector,
                   OrderLine, StockInfo, StoreStock)

_STATUS_MAP = {
    "pending": "processing", "processing": "processing", "holded": "processing",
    "complete": "delivered", "canceled": "cancelled", "closed": "refunded",
}


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace(" ", "T"))
    except ValueError:
        return None


def _criteria(field: str, value: str, condition: str = "eq") -> dict:
    return {
        "searchCriteria[filter_groups][0][filters][0][field]": field,
        "searchCriteria[filter_groups][0][filters][0][value]": value,
        "searchCriteria[filter_groups][0][filters][0][condition_type]": condition,
    }


class _MagentoClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        if not base_url or not token:
            raise ValueError("MAGENTO_BASE_URL and MAGENTO_ACCESS_TOKEN are required for the magento connector")
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/rest/V1",
                                  headers={"Authorization": f"Bearer {token}"}, timeout=timeout)

    def get(self, path: str, params: dict | None = None):
        r = self._http.get(path, params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


class MagentoOrderConnector(OrderConnector):
    name = "magento"

    def __init__(self, base_url: str, token: str):
        self.client = _MagentoClient(base_url, token)

    def _tracking(self, entity_id: int):
        res = self.client.get("/shipments", _criteria("order_id", str(entity_id))) or {}
        for shipment in res.get("items", []):
            for t in shipment.get("tracks", []):
                return t.get("title"), t.get("track_number")
        return None, None

    def _map(self, o: dict) -> Order:
        lines = [OrderLine(i.get("sku", ""), i.get("name", ""), int(i.get("qty_ordered", 0)),
                           float(i.get("price", 0))) for i in o.get("items", []) if not i.get("parent_item_id")]
        status = _STATUS_MAP.get(o.get("status", ""), o.get("status", "processing"))
        carrier, number = self._tracking(o["entity_id"])
        if number and status == "processing":
            status = "shipped"
        shipping = (o.get("extension_attributes", {}).get("shipping_assignments") or [{}])[0]
        method = (shipping.get("shipping") or {}).get("method", "") or ""
        return Order(
            order_id=o.get("increment_id"), status=status, placed_at=_dt(o.get("created_at")) or datetime.utcnow(),
            customer_email=o.get("customer_email"), lines=lines, total=float(o.get("grand_total", 0)),
            fulfilment="click_and_collect" if "pickup" in method or "instore" in method else "delivery",
            carrier=carrier, tracking_number=number,
        )

    def get_order(self, order_id):
        res = self.client.get("/orders", _criteria("increment_id", order_id.strip())) or {}
        items = res.get("items", [])
        return self._map(items[0]) if items else None

    def find_orders_by_email(self, email, limit=5):
        params = _criteria("customer_email", email) | {
            "searchCriteria[sortOrders][0][field]": "created_at",
            "searchCriteria[sortOrders][0][direction]": "DESC",
            "searchCriteria[pageSize]": str(limit)}
        res = self.client.get("/orders", params) or {}
        return [self._map(o) for o in res.get("items", [])]


class MagentoInventoryConnector(InventoryConnector):
    """Uses Magento MSI. Store-level stock requires one MSI source per store."""
    name = "magento-msi"

    def __init__(self, base_url: str, token: str, stock_id: int = 1):
        self.client = _MagentoClient(base_url, token)
        self.stock_id = stock_id

    def get_stock(self, sku):
        product = self.client.get(f"/products/{sku}")
        if not product:
            return None
        salable = self.client.get(f"/inventory/get-product-salable-quantity/{sku}/{self.stock_id}") or 0
        sources = self.client.get("/inventory/source-items", _criteria("sku", sku)) or {}
        stores = [StoreStock(s["source_code"], int(s.get("quantity", 0)))
                  for s in sources.get("items", []) if s.get("source_code") != "default"]
        return StockInfo(sku, product.get("name", sku), float(product.get("price", 0)), int(salable), stores)

    def search_products(self, query, limit=3):
        params = _criteria("name", f"%{query}%", "like") | {"searchCriteria[pageSize]": str(limit)}
        res = self.client.get("/products", params) or {}
        out = []
        for p in res.get("items", []):
            info = self.get_stock(p["sku"])
            if info:
                out.append(info)
        return out


class HTTPCRMConnector(CRMConnector):
    """Generic REST CRM. Expects GET {base}/customers?email=..|phone=.. returning a JSON object
    (or list) with: id, name, email, phone, segment, trade_tier, lifetime_value, account_manager.
    Adapt _map() for Salesforce / Dynamics / HubSpot field names."""
    name = "http-crm"

    def __init__(self, base_url: str, api_key: str | None):
        if not base_url:
            raise ValueError("CRM_BASE_URL is required for the http CRM connector")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=10.0)

    @staticmethod
    def _map(c: dict) -> CustomerRecord:
        return CustomerRecord(str(c.get("id")), c.get("name", ""), c.get("email"), c.get("phone"),
                              c.get("segment", "retail"), c.get("trade_tier"),
                              float(c.get("lifetime_value", 0) or 0), c.get("account_manager"))

    def find_customer(self, email=None, phone=None):
        params = {"email": email} if email else {"phone": phone} if phone else None
        if not params:
            return None
        r = self._http.get("/customers", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            data = data[0] if data else None
        return self._map(data) if data else None
