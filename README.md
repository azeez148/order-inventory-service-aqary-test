# Order & Inventory Service

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

All four services have healthchecks; `docker compose up -d --build --wait` waits for startup. The API healthcheck covers database access; search indexing remains eventually consistent.

To verify committed code from a fresh local clone, run `powershell -ExecutionPolicy Bypass -File scripts/cold_start.ps1`. It builds a unique Compose project with new PostgreSQL/Meilisearch volumes and random host ports, runs smoke/outbox checks, seeds and verifies the full bulk catalog, then runs the mixed load test. It deletes only its own temporary clone and service volumes, even on failure. Docker and Git must be available; cached image layers may be reused. This checks fresh startup and warmed load performance separately, not cold-request latency. Set `API_PORT` or `PGBOUNCER_PORT` to change normal development ports.

## Bulk fixture

```powershell
docker compose run --rm api python -m scripts.bulk_seed
```

This adds 20,000 products, 100,000 historical orders, 100,000 order items and 20,000 outbox events using one atomic `INSERT ... SELECT generate_series` statement. It preserves existing data and uses unique SKUs; each rerun adds another batch. `--products` and `--orders` allow smaller fixtures. Stock 1000 represents remaining inventory after historical sales; the fixture bypasses the live order API. The outbox indexes the new catalog asynchronously. The existing CSV seed remains available for a small demo.

After one bulk batch, `docker compose run --rm api python -m loadtest.catalog` verifies fixture counts, report totals and confirmed indexing. Pass matching counts if using a smaller fixture; the default expects one 20k/100k batch.

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

Product creation, patches, stock adjustments and orders insert `search_outbox` rows in the same database transaction as the product change. A lifespan asyncio worker drains pending rows using `FOR UPDATE SKIP LOCKED`, submits current product documents, polls Meilisearch task completion, and marks rows processed only after success. Failures roll back acknowledgement and retry; shutdown awaits cancellation before closing resources. Delivery is at least once, and document upserts are safe to repeat.

An advisory transaction lock serializes delivery and reindex batches across API processes, preventing snapshots from being submitted out of order. Transactions hold a connection while awaiting indexing (30-second task deadline); this is an intentional MVP trade-off. Startup resubmits existing products for compatibility with older databases; `/admin/reindex` also waits for task success. Search failures return 503. Stock and price are hydrated from PostgreSQL. In production, run the consumer separately or use CDC, and add outbox retention and monitoring.

Focused outbox proof (stop the API consumer to make the failure test deterministic):

```powershell
docker compose stop api
docker compose run --rm --no-deps api python -m loadtest.outbox
docker compose start api
```

## Verification and observed results

The load test sends 500 one-unit orders for one product with 100 stock, 100 product reads and 50 searches in each phase, and five two-second reports in the mixed phase. Default maximum in-flight orders is **50**. Independent HTTP clients model independent tools and prevent reports from consuming a storefront client's HTTP connection slots. Search waits for its unique SKU to become indexed before timing, then requires the expected product, price and nonnegative stock in every search response. Before timing, the script explicitly warms write/read/reporting connections using health checks, product reads and zero-delay reports; `--cold` skips this warm-up (index convergence is still required).

Acceptance targets chosen for this local exercise: order request p95 <= 5000 ms, product-read and search p95 <= 2000 ms, mixed/baseline order p95 <= 2x, exactly 100 successful and 400 rejected orders, final stock zero, and every report/read/search returning 200 with valid search results. Any failed gate gives exit code 1. Targets are configurable via `--order-p95-ms`, `--read-p95-ms`, `--search-p95-ms`, and `--max-slowdown`. Current results are in [RESULTS.md](RESULTS.md).

Fresh-clone startup, smoke/outbox proof, the full bulk fixture, report totals and confirmed catalog indexing passed. With the bulk dataset, the combined load test **failed the absolute read latency target** (baseline p95 2063 ms against 2000 ms); stock correctness, all expected statuses/search results and order latency/isolation gates passed. A trial with 20 read slots did not improve results, so the original ten-slot pool is retained. The cold-start script intentionally propagates benchmark failures with exit code 1 after cleanup; passing functional startup does not mean every performance target passes. Keep these limits visible in a submission.

Historical results before adding the outbox and mixed-load search, using stock 200 and 30 reports, with explicit warm-up, 50 in-flight orders and 10 reserved read connections:

| Phase | Order p50 / p95 | Read p95 | Report p95 | Orders 201 / 409 | Final stock |
|---|---|---|---|---|---|
| A: orders + reads | 586 / 1720 ms | 1191 ms | n/a | 200 / 300 | 0 |
| B: orders + reads + reports | 387 / 1987 ms | 1038 ms | 20710 ms | 200 / 300 | 0 |

All 30 reports and all 100 reads per phase returned 200; mixed/baseline order p95 was 1.16x. All configured gates passed (exit code 0). Phase wall time was 7.47/20.74 seconds. Queue-inclusive order p95 was 6966/6248 ms. Three reporting connections serving thirty two-second sleeps require about twenty seconds in ten batches; this is expected report queueing.

Order request latency starts when an order enters the configured in-flight window. The script also prints `orders incl queue`, including time waiting for that window, and phase wall time. This is a bounded-concurrency workload, not a promise that a simultaneous 500-request burst completes within five seconds.

An earlier stress run with five read connections and `--concurrency 200` preserved stock correctness and returned all expected statuses, with mixed/baseline order p95 1.05x, but baseline/mixed order p95 was 9357/9835 ms: it **failed the absolute latency target**. The original single-client mixed test also showed much worse read latency. The final tested responsive operating point is 50 in-flight orders; the final ten-read-connection configuration has not been benchmarked at 200. Results vary with host load, database size and warm/cold connections; do not run smoke checks concurrently with benchmarks.

With the earlier five-connection read pool, a run without explicit warm-up failed the read target: baseline read p95 was 2159 ms (mixed read p95 822 ms). A further warmed run also missed the baseline read target at 2973 ms; this prompted reserving ten read connections for the storefront. Cold-start read responsiveness is not guaranteed by the final steady-state result; use `--cold` to measure it independently.

The runtime smoke script verifies concurrent multi-product orders (5 successes, 15 conflicts), matching order retrieval, rollback when a later product lacks stock, duplicate-line merging, name/description search and update synchronization, fresh price/stock, and the SQLAlchemy timeout handler returning 503. The outbox proof also checks transactional rollback, failure after task acceptance, and retry through confirmed real indexing.

## Assumptions and next steps

- Development schema creation uses `create_all`; use Alembic migrations for schema evolution.
- No auth, payments, frontend or cloud deployment, as requested by the task.
- No order cancellation or idempotency keys; those need explicit product requirements.
- A read replica or precomputed aggregates would reduce reporting resource contention.
- PgBouncer images/configuration, plaintext development credentials and Meilisearch's development key are local-only choices.
- The Git history contains incremental scaffold, database, order, search, product, load-test and documentation commits. Publish the repository to GitHub and submit its URL after final review.
