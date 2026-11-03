from functools import lru_cache

from ..config import get_settings
from .base import CRMConnector, InventoryConnector, OrderConnector
from .mock import MockCRMConnector, MockInventoryConnector, MockOrderConnector


@lru_cache
def order_connector() -> OrderConnector:
    s = get_settings()
    if s.order_connector == "magento":
        from .magento import MagentoOrderConnector
        return MagentoOrderConnector(s.magento_base_url, s.magento_access_token)
    return MockOrderConnector()


@lru_cache
def crm_connector() -> CRMConnector:
    s = get_settings()
    if s.crm_connector == "http":
        from .magento import HTTPCRMConnector
        return HTTPCRMConnector(s.crm_base_url, s.crm_api_key)
    return MockCRMConnector()


@lru_cache
def inventory_connector() -> InventoryConnector:
    s = get_settings()
    if s.inventory_connector == "magento":
        from .magento import MagentoInventoryConnector
        return MagentoInventoryConnector(s.magento_base_url, s.magento_access_token)
    return MockInventoryConnector()
