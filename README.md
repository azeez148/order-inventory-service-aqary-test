ï»¿# Order & Inventory Service

Async FastAPI API for product browsing/search, warehouse stock writes, orders and heavy reporting on one PostgreSQL database. Stack: SQLAlchemy async + asyncpg, PostgreSQL 16, PgBouncer and Meilisearch.

## Run locally

```powershell
docker compose up -d --build
```

API: http://localhost:8000/docs. Once `/health` returns 200, run:

```powershell
docker compose run --rm api python -m loadtest.smoke
docker compose run --rm api python loadtest/loadtest.py --base-url http://api:8000
```

After changing source, rebuild the API image. During load-test development in PowerShell you can mount the scripts:

```powershell
docker compose run --rm -v "${PWD}/loadtest:/srv/loadtest" api python loadtest/loadtest.py --base-url http://api:8000
```

## API

| Method/path | Purpose |
|---|---|
| POST /products | Create product with SKU, name, description, price and initial stock |
| GET /products/{id}, PATCH /products/{id} | Read product; update name, description or price |
| POST /products/{id}/stock-adjustments | Atomic stock change using a signed `delta` |
| GET /products/search?q= | Indexed name/description/SKU search with fresh database values |
| POST /orders | Atomic multi-product order; 409 for insufficient stock |
| GET /orders/{id} | Order details including line items and purchase prices |
| GET /reports/sales?limit=10&delay=2 | Sales aggregation; optional 0-10 second simulated heavy query |
| POST /admin/reindex | Resubmit product documents to repair missed search updates |
| GET /health | Database connectivity check |

## Architecture and connection handling

Routes handle HTTP, `services/orders.py` owns order transactions, `models.py` and `schemas.py` define persistence and validation, and `search.py` owns search integration.

One asynchronous Uvicorn worker uses three independent SQLAlchemy pools and PgBouncer transaction-pool aliases, all pointing at the same PostgreSQL database:

| Workload | App connections | PgBouncer alias/base server capacity | Checkout timeout |
|---|---|---|---|
| Orders/product/warehouse writes | 15 + 5 overflow | shop / 20 | 10 seconds |
| Storefront reads/search hydration/order retrieval | 10, no overflow | shop_reads / 10 | 10 seconds |
| Reports | 3, no overflow | shop_reporting / 4 | 60 seconds |

PgBouncer additionally permits up to 5 reserve connections per pool after 3 seconds of waiting and limits client connections to 1000. These are development settings for one API instance; capacity must be reconsidered when adding instances. SQLAlchemy checkout exhaustion returns 503 with `Retry-After: 1`. Reports run in read-only transactions with a 30-second per-statement timeout. The longer reporting checkout timeout allows a finite burst of reports to queue.

Separate pools reserve connection capacity for reads and writes. They do not isolate PostgreSQL CPU, I/O, or database locks. Reads have their own pool because hot-product orders can fill a write pool with row-lock waiters. Async endpoints use async database and HTTP calls; awaiting I/O lets other requests progress.

Stock reductions use `UPDATE ... WHERE stock >= quantity RETURNING price`, under a transaction. PostgreSQL serializes writes to the same row and rechecks the predicate, preventing overselling. Duplicate order lines are merged and products are updated in ascending ID order to avoid opposite lock ordering. Any failure rolls back the complete order. Stock adjustments are also conditional atomic updates; a database check constraint prevents negative stock. Successful order responses use already-loaded committed data instead of checking out another connection for two follow-up reads.

Asyncpg prepared-statement caching is disabled and statement names are unique for PgBouncer transaction pooling.

## Search and synchronization

Meilisearch indexes name, description and SKU, providing prefix matching, relevance and typo tolerance without SQL LIKE/ILIKE scans. Search returns IDs; one primary-key database query hydrates products, keeping stock and price fresh.

Product creation/update submits documents in a background task after commit. Indexing is asynchronous and eventually consistent: a new or updated product may take a short time to appear. Startup resubmits products in batches; `/admin/reindex` repairs missed updates. Search failures return 503. Background updates can be lost if the process exits or Meilisearch is unavailable; a transactional outbox and tracking index task completion would improve reliability. The MVP deliberately keeps this trade-off small and explicit.

## Verification and observed results

The load test sends 500 one-unit orders for one product with 200 stock, 100 product reads in each phase, and 30 two-second reports in the mixed phase. Default maximum in-flight orders is **50**. Independent HTTP clients model independent tools and prevent reports from consuming a storefront client's HTTP connection slots. Before timing, it explicitly warms write/read/reporting connections using health checks, product reads and zero-delay reports; `--cold` skips this warm-up.

Acceptance targets chosen for this local exercise: order request p95 <= 5000 ms, product-read p95 <= 2000 ms, mixed/baseline order p95 <= 2x, exactly 200 successful and 300 rejected orders, final stock zero, and every report/read returning 200. Any failed gate gives exit code 1. Targets are configurable via `--order-p95-ms`, `--read-p95-ms`, and `--max-slowdown`.

Observed on the final local Docker Desktop development stack with explicit warm-up, 50 in-flight orders and 10 reserved read connections:

| Phase | Order p50 / p95 | Read p95 | Report p95 | Orders 201 / 409 | Final stock |
|---|---|---|---|---|---|
| A: orders + reads | 586 / 1720 ms | 1191 ms | n/a | 200 / 300 | 0 |
| B: orders + reads + reports | 387 / 1987 ms | 1038 ms | 20710 ms | 200 / 300 | 0 |

All 30 reports and all 100 reads per phase returned 200; mixed/baseline order p95 was 1.16x. All configured gates passed (exit code 0). Phase wall time was 7.47/20.74 seconds. Queue-inclusive order p95 was 6966/6248 ms. Three reporting connections serving thirty two-second sleeps require about twenty seconds in ten batches; this is expected report queueing.

Order request latency starts when an order enters the configured in-flight window. The script also prints `orders incl queue`, including time waiting for that window, and phase wall time. This is a bounded-concurrency workload, not a promise that a simultaneous 500-request burst completes within five seconds.

An earlier stress run with five read connections and `--concurrency 200` preserved stock correctness and returned all expected statuses, with mixed/baseline order p95 1.05x, but baseline/mixed order p95 was 9357/9835 ms: it **failed the absolute latency target**. The original single-client mixed test also showed much worse read latency. The final tested responsive operating point is 50 in-flight orders; the final ten-read-connection configuration has not been benchmarked at 200. Results vary with host load, database size and warm/cold connections; do not run smoke checks concurrently with benchmarks.

With the earlier five-connection read pool, a run without explicit warm-up failed the read target: baseline read p95 was 2159 ms (mixed read p95 822 ms). A further warmed run also missed the baseline read target at 2973 ms; this prompted reserving ten read connections for the storefront. Cold-start read responsiveness is not guaranteed by the final steady-state result; use `--cold` to measure it independently.

The runtime smoke script verified concurrent multi-product orders (5 successes, 15 conflicts), matching order retrieval, rollback when a later product lacks stock, duplicate-line merging, name/description search and update synchronization, fresh price/stock, and the SQLAlchemy timeout handler returning 503. Observed name lookups took 28.7-37.3 ms on the small local dataset; search performance at a large catalog size or under mixed load has not been benchmarked.

## Assumptions and next steps

- Development schema creation uses `create_all`; use Alembic migrations for schema evolution.
- No auth, payments, frontend or cloud deployment, as requested by the task.
- No order cancellation or idempotency keys; those need explicit product requirements.
- A read replica or precomputed aggregates would reduce reporting resource contention.
- PgBouncer images/configuration, plaintext development credentials and Meilisearch's development key are local-only choices.
- The Git history contains incremental scaffold, database, order, search, product, load-test and documentation commits. Publish the repository to GitHub and submit its URL after final review.
