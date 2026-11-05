# pos-offline-sync

[![ci](https://github.com/itsoumya-d/pos-offline-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/itsoumya-d/pos-offline-sync/actions/workflows/ci.yml)

A production-oriented offline-first POS sync service:

- Stores, POS devices, products and stock.
- Idempotent order submission keyed by client order + idempotency key.
- Duplicate-order replay and changed-payload conflict detection.
- Stock reservation with insufficient-stock and unknown-SKU guards.
- Outbox events with retry attempts and dead-letter status.
- Stock reconciliation between expected and recorded inventory.
- API-key RBAC (`viewer`, `cashier`, `admin`), Postgres/SQLite, Alembic,
  Docker and CI.

This is a real backend service, not a till UI. It does **not** claim live POS
terminal, card-reader or accounting-provider integration; those adapters are
the seam.

## API

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/health` | none | Liveness |
| GET | `/ready` | none | Database readiness |
| POST | `/stores` | admin | Create a store |
| POST | `/products` | admin | Create/update product and stock |
| POST | `/devices` | admin | Register a POS device |
| POST | `/orders` | cashier | Submit an idempotent order |
| POST | `/outbox/flush` | cashier | Sync pending outbox events |
| POST | `/reconcile` | viewer | Compare expected stock |
| GET | `/audit` | admin | Audit trail |

Send `X-API-Key: <key>` on protected requests.

## Tests

```bash
pytest
```

Covers store/product/device setup, stock decrement, idempotent replay,
payload conflicts, insufficient stock, unknown SKU, device/store mismatch,
outbox sync, reconciliation and audit.

## License

MIT
