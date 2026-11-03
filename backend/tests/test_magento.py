"""Magento 2 REST adapter against recorded-shape responses (no network)."""
import httpx

from app.connectors.magento import MagentoOrderConnector

ORDER = {"items": [{
    "entity_id": 77, "increment_id": "BL100099001", "status": "processing", "created_at": "2026-10-10 03:12:00",
    "customer_email": "sam@example.com", "grand_total": 418.0,
    "items": [{"sku": "BL-FAN-52W", "name": "Aero 52 DC Fan", "qty_ordered": 1, "price": 399.0},
              {"sku": "CHILD", "name": "child row", "qty_ordered": 1, "price": 0, "parent_item_id": 5}],
    "extension_attributes": {"shipping_assignments": [{"shipping": {"method": "flatrate_flatrate"}}]},
}]}
SHIPMENTS = {"items": [{"tracks": [{"title": "Australia Post", "track_number": "33LPK000111"}]}]}


def handler(request: httpx.Request):
    assert request.headers["Authorization"] == "Bearer tok"
    if request.url.path.endswith("/orders"):
        return httpx.Response(200, json=ORDER)
    if request.url.path.endswith("/shipments"):
        return httpx.Response(200, json=SHIPMENTS)
    return httpx.Response(404)


def test_magento_order_mapping():
    c = MagentoOrderConnector("https://shop.example", "tok")
    c.client._http = httpx.Client(base_url="https://shop.example/rest/V1", transport=httpx.MockTransport(handler),
                                  headers={"Authorization": "Bearer tok"})
    o = c.get_order("BL100099001")
    assert o.order_id == "BL100099001" and o.status == "shipped"  # processing + tracking = shipped
    assert o.carrier == "Australia Post" and o.tracking_number == "33LPK000111"
    assert len(o.lines) == 1 and o.total == 418.0 and o.fulfilment == "delivery"
