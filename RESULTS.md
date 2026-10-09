# Verification results

Measured on 2026-10-10 on the local Docker Desktop stack. Host load affects timings.

## Durable indexing

`python -m loadtest.outbox` passed with the API consumer stopped:

- Product changes and outbox rows commit/roll back together.
- HTTP acceptance followed by a failed Meilisearch task leaves rows pending.
- Retrying with real Meilisearch confirms task success before acknowledgement; the product is searchable.

`python -m loadtest.smoke` passed multi-product concurrency, rollback, duplicate lines, order retrieval, search updates and pool exhaustion handling.

## Orders, reads, search and reporting (small existing dataset)

Command: `docker compose run --rm api python loadtest/loadtest.py --base-url http://api:8000`

Each phase sends 500 orders against stock 100, with 50 maximum in-flight orders, 100 product reads and 50 searches. Phase B adds five reports with a two-second database sleep. Index convergence occurs before timing; every search must return its unique test product with current price and nonnegative stock.

| Phase | Order p50 / p95 | Read p95 | Search p95 | Report p95 | Orders 201 / 409 | Final stock |
|---|---|---|---|---|---|---|
| A | 548 / 2268 ms | 1794 ms | 1416 ms | — | 100 / 400 | 0 |
| B | 493 / 2654 ms | 1878 ms | 1874 ms | 4408 ms | 100 / 400 | 0 |

All reads, searches and reports returned 200, and every search result check passed. Mixed/baseline order p95 was 1.17x. All default gates passed, exit code 0. Phase wall time: 8.16 / 8.30 seconds. Order p95 including waiting for the concurrency window: 7708 / 7680 ms.

These timings use explicit connection warm-up, an existing database and simulated report delay. They do not establish fresh-start latency or bulk-catalog performance.

## Bulk fixture

`docker compose run --rm api python -m scripts.bulk_seed` inserted 20,000 products, 100,000 orders, 100,000 order items and 20,000 outbox rows in 21.98 seconds, preserving existing data.

`docker compose run --rm api python -m loadtest.catalog` passed:

- Exact bulk product/order/item counts, matching order/item revenue and nonnegative stock.
- Real sales aggregation without simulated delay agrees with database totals.
- All bulk outbox rows processed after confirmed indexing, a bulk SKU searchable, and Meilisearch document count matching PostgreSQL.

## Initial fresh-clone run: read capacity limit

Commit `211c2d3` built and started all four healthy services from a local clone with fresh PostgreSQL/Meilisearch volumes. Smoke and outbox checks passed, the bulk seed completed in 22.05 seconds, and catalog/report/index checks passed.

The subsequent load test, with ten reserved read connections and 50 in-flight orders, exited 1: phase A read p95 was 2063 ms, above the unchanged 2000 ms target. Phase B read/search p95 was 1707/1761 ms. Orders remained correct (100 successes, 400 conflicts, stock zero in both phases); order p95 was 2833/2511 ms and mixed/baseline slowdown was 0.89x.

A diagnostic run at 25 in-flight orders on the existing bulk stack improved order p95 to 1280/1682 ms, but mixed read/search p95 was 2207/2189 ms and still failed. Reducing order concurrency alone did not establish the read/search target. This motivated increasing the matching application and PgBouncer read pools to 20; order concurrency, probe counts and all latency gates remain unchanged.
