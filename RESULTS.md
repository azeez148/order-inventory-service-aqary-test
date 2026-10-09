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
