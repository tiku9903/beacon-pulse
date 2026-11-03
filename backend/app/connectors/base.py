"""Connector contracts. The pipeline only ever talks to these interfaces, so swapping the
mock systems for Beacon's real Magento / CRM / inventory is a config change, not a rewrite."""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class OrderLine:
    sku: str
    name: str
    qty: int
    unit_price: float


@dataclass
class Order:
    order_id: str
    status: str  # processing|ready_for_pickup|shipped|delivered|cancelled|refunded
    placed_at: datetime
    customer_email: str | None
    lines: list[OrderLine]
    total: float
    fulfilment: str = "delivery"  # delivery|click_and_collect
    pickup_store: str | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    tracking_url: str | None = None
    eta: datetime | None = None
    delivered_at: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("placed_at", "eta", "delivered_at"):
            if d[k]:
                d[k] = d[k].isoformat()
        return d


@dataclass
class CustomerRecord:
    crm_id: str
    name: str
    email: str | None = None
    phone: str | None = None
    segment: str = "retail"  # retail|trade
    trade_tier: str | None = None
    lifetime_value: float = 0.0
    account_manager: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class StoreStock:
    store: str
    qty: int


@dataclass
class StockInfo:
    sku: str
    name: str
    price: float
    online_qty: int
    stores: list[StoreStock]
    restock_eta: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["restock_eta"]:
            d["restock_eta"] = d["restock_eta"].isoformat()
        return d


class OrderConnector(ABC):
    name = "abstract"

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None: ...

    @abstractmethod
    def find_orders_by_email(self, email: str, limit: int = 5) -> list[Order]: ...


class CRMConnector(ABC):
    name = "abstract"

    @abstractmethod
    def find_customer(self, email: str | None = None, phone: str | None = None) -> CustomerRecord | None: ...


class InventoryConnector(ABC):
    name = "abstract"

    @abstractmethod
    def get_stock(self, sku: str) -> StockInfo | None: ...

    @abstractmethod
    def search_products(self, query: str, limit: int = 3) -> list[StockInfo]: ...
