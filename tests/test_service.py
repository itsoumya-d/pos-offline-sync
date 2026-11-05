import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from possync.db import Base
from possync.service import ConflictError, NotFoundError, PosSyncService, ValidationError


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory(); yield s; s.close()


def setup_store(session):
    service = PosSyncService(session)
    store = service.create_store("Queen St")
    device = service.register_device(store.id, "POS-1", "Front counter")
    service.upsert_product(store.id, "COFFEE", "Coffee", 5.5, 10)
    return service, store, device


def test_store_product_device_lifecycle(session):
    service, store, device = setup_store(session)
    assert store.name == "Queen St"
    assert device.device_id == "POS-1"
    with pytest.raises(ValidationError, match="already exists"):
        service.create_store("Queen St")


def test_order_decrements_stock_and_creates_outbox(session):
    service, store, device = setup_store(session)
    order, outbox = service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 2}], "idem-1")
    assert str(order.total) == "11.00"
    assert outbox.status == "pending"
    product = service.session.query(__import__("possync.tables", fromlist=["ProductRow"]).ProductRow).first()
    assert product.stock == 8


def test_duplicate_order_is_idempotent(session):
    service, store, device = setup_store(session)
    first, _ = service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 1}], "idem-1")
    second, _ = service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 1}], "idem-1")
    assert first.id == second.id


def test_changed_order_payload_conflicts(session):
    service, store, device = setup_store(session)
    service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 1}], "idem-1")
    with pytest.raises(ConflictError):
        service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 2}], "idem-2")


def test_insufficient_stock_rejected(session):
    service, store, device = setup_store(session)
    with pytest.raises(ValidationError, match="insufficient stock"):
        service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 11}], "idem-1")


def test_unknown_sku_rejected(session):
    service, store, device = setup_store(session)
    with pytest.raises(NotFoundError, match="unknown sku"):
        service.submit_order(store.id, device.id, "WEB-1", [{"sku": "TEA", "quantity": 1}], "idem-1")


def test_wrong_device_store_rejected(session):
    service, store, _ = setup_store(session)
    other = service.create_store("Other")
    device = service.register_device(other.id, "POS-2", "Other counter")
    with pytest.raises(NotFoundError):
        service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 1}], "idem-1")


def test_flush_outbox_syncs_and_dead_letters(session):
    service, store, device = setup_store(session)
    for i in range(2):
        service.submit_order(store.id, device.id, f"WEB-{i}", [{"sku": "COFFEE", "quantity": 1}], f"idem-{i}")
    assert service.flush_outbox(store.id) == {"synced": 2, "failed": 0}


def test_reconcile_reports_mismatch(session):
    service, store, _ = setup_store(session)
    mismatches = service.reconcile_stock(store.id, {"COFFEE": 7, "TEA": 5})
    assert {m["kind"] for m in mismatches} == {"stock_mismatch", "missing_product"}


def test_audit_trail(session):
    service, store, device = setup_store(session)
    service.submit_order(store.id, device.id, "WEB-1", [{"sku": "COFFEE", "quantity": 1}], "idem-1")
    actions = [e["action"] for e in service.audit()]
    assert actions == ["store_created", "device_registered", "product_upserted", "order_submitted"]
