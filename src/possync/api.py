from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from .auth import load_api_keys, require_role
from .db import SessionLocal, init_db
from .service import ConflictError, NotFoundError, PosSyncService, ValidationError
from .tables import StoreRow
from sqlalchemy import select


class StoreIn(BaseModel):
    name: str


class ProductIn(BaseModel):
    store_id: int
    sku: str
    name: str
    price: float
    stock: int


class DeviceIn(BaseModel):
    store_id: int
    device_id: str
    name: str


class OrderIn(BaseModel):
    store_id: int
    device_id: int
    client_order_id: str
    idempotency_key: str
    lines: list[dict[str, Any]]


class ReconcileIn(BaseModel):
    store_id: int
    expected: dict[str, int] = Field(default_factory=dict)


def create_app(session_factory: sessionmaker = SessionLocal, api_keys: dict[str, str] | None = None, init_database: bool = True) -> FastAPI:
    if init_database:
        init_db()
    keys = api_keys or load_api_keys()
    app = FastAPI(title="POS offline sync", version="1.0.0")

    def db_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    viewer = require_role("viewer", keys)
    cashier = require_role("cashier", keys)
    admin = require_role("admin", keys)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(session: Session = Depends(db_session)) -> dict[str, str]:
        session.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ready"}

    @app.post("/stores")
    def create_store(payload: StoreIn, session: Session = Depends(db_session), actor: str = Depends(admin)) -> dict[str, Any]:
        try:
            row = PosSyncService(session, actor).create_store(payload.name)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": row.id, "name": row.name}

    @app.post("/products")
    def upsert_product(payload: ProductIn, session: Session = Depends(db_session), actor: str = Depends(admin)) -> dict[str, Any]:
        try:
            row = PosSyncService(session, actor).upsert_product(payload.store_id, payload.sku, payload.name, payload.price, payload.stock)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": row.id, "sku": row.sku, "stock": row.stock}

    @app.post("/devices")
    def register_device(payload: DeviceIn, session: Session = Depends(db_session), actor: str = Depends(admin)) -> dict[str, Any]:
        try:
            row = PosSyncService(session, actor).register_device(payload.store_id, payload.device_id, payload.name)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": row.id, "device_id": row.device_id}

    @app.post("/orders")
    def submit_order(payload: OrderIn, session: Session = Depends(db_session), actor: str = Depends(cashier)) -> dict[str, Any]:
        try:
            order, outbox = PosSyncService(session, actor).submit_order(
                payload.store_id, payload.device_id, payload.client_order_id, payload.lines, payload.idempotency_key
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"order_id": order.id, "total": str(order.total), "outbox_id": outbox.id}

    @app.post("/outbox/flush")
    def flush_outbox(payload: StoreIn, session: Session = Depends(db_session), actor: str = Depends(cashier)) -> dict[str, Any]:
        store = session.scalar(select(StoreRow).where(StoreRow.name == payload.name))
        if store is None:
            raise HTTPException(status_code=404, detail="store not found")
        return PosSyncService(session, actor).flush_outbox(store.id)

    @app.post("/reconcile")
    def reconcile(payload: ReconcileIn, session: Session = Depends(db_session), _: str = Depends(viewer)) -> dict[str, Any]:
        return {"mismatches": PosSyncService(session).reconcile_stock(payload.store_id, payload.expected)}

    @app.get("/audit")
    def audit(session: Session = Depends(db_session), _: str = Depends(admin)) -> dict[str, Any]:
        return {"audit": PosSyncService(session).audit()}

    return app


app = create_app()
