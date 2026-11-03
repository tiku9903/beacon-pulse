"""Seeded stand-ins for Magento (orders), Customer 360 (CRM) and Live Stock (inventory).

Dates are relative to "now" so the demo always looks current. All data is fictional."""
import re
from datetime import datetime, timedelta

from .base import (CRMConnector, CustomerRecord, InventoryConnector, Order, OrderConnector,
                   OrderLine, StockInfo, StoreStock)

STORES = ["Richmond VIC", "Moorabbin VIC", "Alexandria NSW", "Castle Hill NSW",
          "Fortitude Valley QLD", "Osborne Park WA", "Mile End SA"]

PRODUCTS = [
    # sku, name, price, online, per-store qty (aligned with STORES), restock days
    ("BL-FAN-52W", "Aero 52\" DC Ceiling Fan with LED Light - White", 399.00, 42, [6, 3, 0, 5, 2, 4, 1], None),
    ("BL-FAN-48B", "Breeze 48\" AC Ceiling Fan - Matt Black", 189.00, 0, [0, 0, 1, 0, 0, 0, 0], 12),
    ("BL-DL-10W3", "Trio 10W Tri-Colour LED Downlight - 10 Pack", 129.00, 310, [40, 22, 35, 18, 27, 30, 12], None),
    ("BL-PEN-RAT", "Coastal Rattan Pendant 45cm", 149.00, 8, [2, 0, 1, 0, 3, 0, 0], None),
    ("BL-SMT-BLB", "Smart Wi-Fi A60 Colour Bulb E27", 24.95, 520, [60, 44, 58, 31, 40, 39, 25], None),
    ("BL-EXT-WAL", "Harbour IP65 Exterior Wall Light - Black", 89.00, 0, [0, 0, 0, 0, 0, 0, 0], 21),
    ("BL-TRK-4HD", "Linea 4-Light Track Kit - White", 249.00, 15, [1, 2, 0, 0, 1, 1, 0], None),
    ("BL-STR-5M", "Glow 5m Smart LED Strip Kit", 79.00, 96, [10, 8, 12, 4, 9, 7, 5], None),
]

CUSTOMERS = [
    CustomerRecord("CRM-10021", "Sarah Mitchell", "sarah.mitchell@example.com", "+61412000111",
                   "retail", None, 1240.0),
    CustomerRecord("CRM-10022", "James O'Connor", "james.oconnor@example.com", "+61412000222",
                   "retail", None, 389.0),
    CustomerRecord("CRM-20031", "Bright Spark Electrical Pty Ltd", "accounts@brightspark.example",
                   "+61398765432", "trade", "platinum", 186_400.0, "Dana Wu",
                   ["Commercial fit-outs, Melbourne CBD", "30-day account terms"]),
    CustomerRecord("CRM-20032", "Coastline Builders", "orders@coastlinebuilders.example", "+61755551234",
                   "trade", "gold", 64_900.0, "Mark Ellis"),
    CustomerRecord("CRM-10023", "Priya Raman", "priya.raman@example.com", "+61412000333",
                   "retail", None, 2310.0),
    CustomerRecord("CRM-10024", "Tom Nguyen", "tom.nguyen@example.com", "+61412000444",
                   "retail", None, 129.0),
]


def _orders(now: datetime) -> list[Order]:
    p = {sku: (name, price) for sku, name, price, *_ in PRODUCTS}

    def line(sku, qty=1):
        return OrderLine(sku, p[sku][0], qty, p[sku][1])

    def total(lines):
        return round(sum(l.qty * l.unit_price for l in lines), 2)

    rows = [
        ("BL100045821", "shipped", 3, "sarah.mitchell@example.com", [line("BL-FAN-52W")], "delivery", None,
         "Australia Post", "33LPK1290045", now + timedelta(days=1), None),
        ("BL100045822", "processing", 1, "james.oconnor@example.com", [line("BL-DL-10W3", 2)], "delivery", None,
         None, None, now + timedelta(days=4), None),
        ("BL100045823", "ready_for_pickup", 2, "priya.raman@example.com", [line("BL-PEN-RAT", 2)],
         "click_and_collect", "Richmond VIC", None, None, None, None),
        ("BL100045824", "delivered", 9, "tom.nguyen@example.com", [line("BL-DL-10W3")], "delivery", None,
         "StarTrack", "STK99812345", None, now - timedelta(days=5)),
        ("BL100045825", "shipped", 2, "accounts@brightspark.example",
         [line("BL-DL-10W3", 24), line("BL-TRK-4HD", 6)], "delivery", None,
         "StarTrack", "STK99812399", now + timedelta(days=2), None),
        ("BL100045826", "delivered", 40, "priya.raman@example.com", [line("BL-FAN-52W")], "delivery", None,
         "Australia Post", "33LPK1290101", None, now - timedelta(days=36)),
        ("BL100045827", "delivered", 300, "sarah.mitchell@example.com", [line("BL-FAN-48B")], "delivery", None,
         "Australia Post", "33LPK1188810", None, now - timedelta(days=295)),
        ("BL100045828", "processing", 6, "orders@coastlinebuilders.example", [line("BL-EXT-WAL", 18)],
         "delivery", None, None, None, now + timedelta(days=21), None),
    ]
    out = []
    for oid, status, days_ago, email, lines, ful, store, carrier, trk, eta, delivered in rows:
        url = None
        if carrier == "Australia Post":
            url = f"https://auspost.com.au/mypost/track/details/{trk}"
        elif carrier == "StarTrack":
            url = f"https://startrack.com.au/track/details/{trk}"
        out.append(Order(oid, status, now - timedelta(days=days_ago), email, lines, total(lines), ful, store,
                         carrier, trk, url, eta, delivered))
    return out


class MockOrderConnector(OrderConnector):
    name = "mock-magento"

    def _all(self):
        return _orders(datetime.utcnow())

    def get_order(self, order_id: str) -> Order | None:
        key = re.sub(r"[^0-9A-Z]", "", order_id.upper())
        if key.isdigit():
            key = "BL" + key
        return next((o for o in self._all() if o.order_id == key), None)

    def find_orders_by_email(self, email: str, limit: int = 5) -> list[Order]:
        rows = [o for o in self._all() if o.customer_email and o.customer_email.lower() == email.lower()]
        return sorted(rows, key=lambda o: o.placed_at, reverse=True)[:limit]


class MockCRMConnector(CRMConnector):
    name = "mock-customer360"

    def find_customer(self, email=None, phone=None):
        for c in CUSTOMERS:
            if email and c.email and c.email.lower() == email.lower():
                return c
            if phone and c.phone and re.sub(r"\D", "", c.phone)[-9:] == re.sub(r"\D", "", phone)[-9:]:
                return c
        return None


class MockInventoryConnector(InventoryConnector):
    name = "mock-live-stock"

    def _info(self, row) -> StockInfo:
        sku, name, price, online, stores, restock = row
        eta = datetime.utcnow() + timedelta(days=restock) if restock else None
        return StockInfo(sku, name, price, online, [StoreStock(s, q) for s, q in zip(STORES, stores)], eta)

    def get_stock(self, sku):
        row = next((r for r in PRODUCTS if r[0].lower() == sku.lower()), None)
        return self._info(row) if row else None

    def search_products(self, query, limit=3):
        words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
        scored = []
        for r in PRODUCTS:
            hay = (r[0] + " " + r[1]).lower()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [self._info(r) for _, r in scored[:limit]]
