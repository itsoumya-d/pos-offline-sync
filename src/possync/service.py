from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .tables import AuditRow, DeviceRow, OrderRow, OutboxRow, ProductRow, StoreRow

MAX_ATTEMPTS = 3


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ConflictError(Exception):
    pass


class PosSyncService:
    def __init__(self, session: Session, actor: str = "system") -> None:
        self.session = session
        self.actor = actor

    def _audit(self, action: str, reference: str, detail: str = "") -> None:
        self.session.add(AuditRow(action=action, actor=self.actor, reference=reference, detail=detail))

    def create_store(self, name: str) -> StoreRow:
        if not name.strip():
            raise ValidationError("store name is required")
        if self.session.scalar(select(StoreRow).where(StoreRow.name == name)):
            raise ValidationError("store already exists")
        row = StoreRow(name=name)
        self.session.add(row)
        self.session.flush()
        self._audit("store_created", name)
        self.session.commit()
        return row

    def upsert_product(self, store_id: int, sku: str, name: str, price: float, stock: int) -> ProductRow:
        if self.session.get(StoreRow, store_id) is None:
            raise NotFoundError("store not found")
        if not sku.strip() or not name.strip() or price < 0 or stock < 0:
            raise ValidationError("sku/name required; price/stock non-negative")
        row = self.session.scalar(select(ProductRow).where(ProductRow.store_id == store_id, ProductRow.sku == sku))
        if row is None:
            row = ProductRow(store_id=store_id, sku=sku, name=name, price=price, stock=stock)
            self.session.add(row)
        else:
            row.name = name
            row.price = price
            row.stock = stock
        self.session.flush()
        self._audit("product_upserted", f"{store_id}/{sku}")
        self.session.commit()
        return row

    def register_device(self, store_id: int, device_id: str, name: str) -> DeviceRow:
        if self.session.get(StoreRow, store_id) is None:
            raise NotFoundError("store not found")
        if not device_id.strip() or not name.strip():
            raise ValidationError("device_id and name are required")
        row = self.session.scalar(select(DeviceRow).where(DeviceRow.store_id == store_id, DeviceRow.device_id == device_id))
        if row is None:
            row = DeviceRow(store_id=store_id, device_id=device_id, name=name)
            self.session.add(row)
        else:
            row.name = name
        row.last_seen_at = datetime.now(timezone.utc)
        self.session.flush()
        self._audit("device_registered", f"{store_id}/{device_id}")
        self.session.commit()
        return row

    def submit_order(self, store_id: int, device_id: int, client_order_id: str, lines: list[dict], idempotency_key: str) -> tuple[OrderRow, OutboxRow]:
        if not client_order_id.strip() or not idempotency_key.strip() or not lines:
            raise ValidationError("client_order_id, idempotency_key and lines are required")
        store = self.session.get(StoreRow, store_id)
        device = self.session.get(DeviceRow, device_id)
        if store is None or device is None or device.store_id != store_id:
            raise NotFoundError("store or device not found")

        existing = self.session.scalar(select(OrderRow).where(OrderRow.client_order_id == client_order_id))
        if existing is not None:
            outbox = self.session.scalar(select(OutboxRow).where(OutboxRow.idempotency_key == idempotency_key))
            if outbox is not None and outbox.payload == json.dumps({"lines": lines}, sort_keys=True):
                return existing, outbox
            raise ConflictError("client_order_id already used with a different payload")

        total = Decimal("0.00")
        for line in lines:
            sku = str(line.get("sku") or "")
            quantity = int(line.get("quantity", 0))
            if quantity <= 0:
                raise ValidationError("quantity must be positive")
            product = self.session.scalar(select(ProductRow).where(ProductRow.store_id == store_id, ProductRow.sku == sku))
            if product is None:
                raise NotFoundError(f"unknown sku: {sku}")
            if product.stock < quantity:
                raise ValidationError(f"insufficient stock for {sku}")
            product.stock -= quantity
            total += Decimal(str(product.price)) * quantity

        order = OrderRow(store_id=store_id, device_id=device_id, client_order_id=client_order_id, total=total)
        self.session.add(order)
        self.session.flush()
        payload = json.dumps({"lines": lines}, sort_keys=True)
        outbox = OutboxRow(
            store_id=store_id,
            event_type="order.paid",
            idempotency_key=idempotency_key,
            payload=payload,
        )
        self.session.add(outbox)
        self._audit("order_submitted", client_order_id, f"total={total}")
        self.session.commit()
        return order, outbox

    def flush_outbox(self, store_id: int) -> dict[str, int]:
        rows = list(self.session.scalars(select(OutboxRow).where(OutboxRow.store_id == store_id, OutboxRow.status == "pending")).all())
        synced = 0
        failed = 0
        for row in rows:
            row.attempts += 1
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "dead_letter"
                row.last_error = "max attempts reached"
                failed += 1
            else:
                row.status = "synced"
                synced += 1
        self._audit("outbox_flushed", str(store_id), f"synced={synced} failed={failed}")
        self.session.commit()
        return {"synced": synced, "failed": failed}

    def reconcile_stock(self, store_id: int, expected: dict[str, int]) -> list[dict]:
        mismatches = []
        for sku, expected_stock in expected.items():
            product = self.session.scalar(select(ProductRow).where(ProductRow.store_id == store_id, ProductRow.sku == sku))
            if product is None:
                mismatches.append({"sku": sku, "kind": "missing_product", "expected": expected_stock, "actual": None})
            elif product.stock != expected_stock:
                mismatches.append({"sku": sku, "kind": "stock_mismatch", "expected": expected_stock, "actual": product.stock})
        return mismatches

    def audit(self) -> list[dict]:
        rows = self.session.scalars(select(AuditRow).order_by(AuditRow.id)).all()
        return [
            {"at": r.at.isoformat(), "action": r.action, "actor": r.actor, "reference": r.reference, "detail": r.detail}
            for r in rows
        ]
