import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from possync.api import create_app
from possync.db import Base


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(session_factory=factory, api_keys={"admin-key": "admin", "cashier-key": "cashier", "viewer-key": "viewer"}, init_database=False)
    return TestClient(app)


def test_api_requires_auth(client):
    assert client.post("/stores", json={"name": "Queen St"}).status_code == 401
    assert client.post("/stores", json={"name": "Queen St"}, headers={"X-API-Key": "viewer-key"}).status_code == 403


def test_api_full_pos_flow(client):
    admin = {"X-API-Key": "admin-key"}; cashier = {"X-API-Key": "cashier-key"}
    store = client.post("/stores", json={"name": "Queen St"}, headers=admin); store_id = store.json()["id"]
    device = client.post("/devices", json={"store_id": store_id, "device_id": "POS-1", "name": "Counter"}, headers=admin); device_id = device.json()["id"]
    client.post("/products", json={"store_id": store_id, "sku": "COFFEE", "name": "Coffee", "price": 5.5, "stock": 10}, headers=admin)
    order = client.post("/orders", json={"store_id": store_id, "device_id": device_id, "client_order_id": "WEB-1", "idempotency_key": "idem-1", "lines": [{"sku": "COFFEE", "quantity": 2}]}, headers=cashier)
    assert order.status_code == 200
    assert order.json()["total"] == "11.00"
    flush = client.post("/outbox/flush", json={"name": "Queen St"}, headers=cashier)
    assert flush.status_code == 200
    assert flush.json()["synced"] == 1
    reconcile = client.post("/reconcile", json={"store_id": store_id, "expected": {"COFFEE": 7}}, headers={"X-API-Key": "viewer-key"})
    assert reconcile.status_code == 200


def test_api_validation_and_conflict(client):
    admin = {"X-API-Key": "admin-key"}; cashier = {"X-API-Key": "cashier-key"}
    store = client.post("/stores", json={"name": "Queen St"}, headers=admin); store_id = store.json()["id"]
    device = client.post("/devices", json={"store_id": store_id, "device_id": "POS-1", "name": "Counter"}, headers=admin); device_id = device.json()["id"]
    client.post("/products", json={"store_id": store_id, "sku": "COFFEE", "name": "Coffee", "price": 5.5, "stock": 1}, headers=admin)
    bad = client.post("/orders", json={"store_id": store_id, "device_id": device_id, "client_order_id": "WEB-1", "idempotency_key": "idem-1", "lines": [{"sku": "COFFEE", "quantity": 2}]}, headers=cashier)
    assert bad.status_code == 422
