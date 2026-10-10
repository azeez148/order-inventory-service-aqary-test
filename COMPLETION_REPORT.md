# Interview task completion report

Review date: 10 October 2026  
Scope: the supplied FastAPI Order & Inventory Service interview assignment.

## Overall status

The core implementation and requested project deliverables are present. Recorded small-dataset tests pass stock correctness and the project's responsiveness targets. Bulk-data tests preserve correctness but miss read/search latency targets, so performance verification is partially complete. Final submission and repository accessibility still need confirmation.

This report is based on source inspection, Git history, README.md, RESULTS.md and a current Docker service-status check. Existing benchmark results were reviewed, not rerun for this report. PostgreSQL, PgBouncer, Meilisearch and the API were all healthy at review time; healthchecks alone do not prove the complete application behavior.

## Completed work

| Assignment requirement | Status | Evidence |
|---|---|---|
| Async FastAPI application | Implemented | `app/main.py`, async routes, SQLAlchemy/asyncpg database operations and async HTTP search calls. |
| Product creation and updates | Implemented | `app/routers/products.py`: create/read/patch products with SKU, name, description, price and stock. |
| Warehouse inventory changes | Implemented | Signed stock-adjustment endpoint uses a conditional atomic update; negative stock is prevented. |
| Search by name/description without SQL LIKE/ILIKE scans | Implemented | Meilisearch indexes name, description and SKU; results are hydrated from PostgreSQL for current price/stock. |
| Keep search synchronized | Implemented; recorded checks passed | Transactional outbox, retries, confirmation of indexing task success, startup reindex and manual reindex. See `app/search.py` and `loadtest/outbox.py`. |
| Place single/multiple-product orders safely | Implemented; recorded checks passed | Conditional stock reductions, one transaction, duplicate-line merging, consistent product lock order and rollback on failure in `app/services/orders.py`. |
| Retrieve order details | Implemented; recorded checks passed | `GET /orders/{id}` returns line items and purchase prices. |
| Heavier reporting endpoint | Implemented; recorded checks passed | `GET /reports/sales` aggregates sales across products/orders. Optional database sleep models slower reporting; bulk checks also verify real totals without the delay. |
| Database connection strategy | Implemented and documented | Separate bounded write/read/report pools through PgBouncer transaction pooling; reporting transactions are read-only with statement timeouts. App pool checkout exhaustion has a 503 handler. |
| Concurrent orders and reports load test | Delivered; recorded small-dataset run passed | `loadtest/loadtest.py` compares baseline and mixed workloads, with concurrent reads/search, stock assertions and latency gates. Bulk performance remains partial; see below. |
| Full local stack with one command | Delivered; recorded fresh-start checks passed | `docker compose up -d --build`; Compose defines all four services and healthchecks. |
| Load-test result notes | Delivered | `RESULTS.md` records passing and failing runs, dataset sizes, latencies and limitations. |
| README setup, architecture and trade-offs | Delivered | Includes connection capacity, search consistency, operating limits, commands, assumptions and future improvements. |
| Clear code separation | Present | Separate routes, order service, database/configuration, models/schemas and search integration. |
| Incremental Git commits | Completed | History contains distinct infrastructure, models, APIs, search, tests and documentation commits. |

Additional completed work includes a bulk fixture of 20,000 products and 100,000 historical orders/items, catalog/index consistency checks and an isolated fresh-clone verification script.

## Pending or partially complete work within the doable scope

| Priority | Item | Current evidence | Completion condition |
|---|---|---|---|
| High | Resolve or explicitly accept the bulk-load responsiveness limitation | The ten-read-slot fresh run recorded baseline read p95 of 2063 ms against a 2000 ms target. A twenty-slot trial also failed read/search targets; the final configuration restores ten slots. | Investigate latency and repeat the unchanged workload/gates after any justified adjustment, or submit the existing partial result with the limitation clearly stated. The assignment permits well-explained partial solutions. |
| Medium | Verify the exact final revision if further implementation changes are made | Recorded fresh-clone checks cover earlier commits; current services are healthy, but this report did not rerun the full suite on HEAD. | Record the tested commit, run smoke/outbox/catalog checks and the mixed benchmark, and retain both passing and failing results. |
| Medium | Confirm GitHub access for the evaluator | Origin is `https://github.com/azeez148/order-inventory-service-aqary-test`. Local HEAD matches the locally stored `origin/main` reference. Live remote contents and public/shared access were not checked. | Confirm the current work is on GitHub and the evaluator can access the repository. |
| High before handoff | Send the repository URL as the final submission | A GitHub remote is configured; the review provides no evidence that the candidate has sent the submission reply. | Reply through the required submission channel with the accessible repository URL. |

The assignment does not prescribe the project's numerical latency thresholds or require the added bulk fixture. Bulk latency is an observed limitation against self-chosen targets, not a missing API feature. Cold-request latency and higher-concurrency performance also remain unproven; the passing small-dataset result uses warmed connections and 50 maximum in-flight orders.

## Recorded verification snapshot

| Check | Recorded result |
|---|---|
| Smoke checks | Passed concurrent multi-product orders, retrieval, rollback, duplicate merging, search updates and a simulated pool-timeout response. |
| Outbox checks | Passed transactional rollback, simulated indexing-task failure after acceptance, and retry with confirmed real indexing. |
| Small-dataset mixed benchmark | Passed all default gates: each phase produced 100 successful orders, 400 stock conflicts and final stock zero. Mixed p95: orders 2654 ms, reads 1878 ms, search 1874 ms. |
| Fresh startup and bulk catalog | Recorded fresh runs passed service startup, functional checks, exact fixture counts, report totals and indexing consistency. |
| Bulk benchmark | Stock/status/search-result correctness passed; absolute read/search latency targets were not consistently met. |
| Current local service health | All four services healthy during this review. |

## Optional improvements, not required to finish this assignment

- Alembic migrations instead of development-time `create_all`.
- A separate indexing consumer, outbox retention and monitoring.
- Precomputed reporting aggregates or a read replica if profiling justifies them.
- Revisit connection capacity when running multiple API instances.

Authentication, user accounts, payments, frontend UI and cloud/Kubernetes deployment are explicitly out of scope. Order cancellation and idempotency are future product decisions, not unfinished requirements here. Compliance with the three-day deadline cannot be established without the task receipt date.

## Handoff assessment

The project is ready for a candid partial-performance submission once repository access and the submission reply are confirmed. Its main unresolved engineering issue is consistent read/search latency with the bulk dataset. Keep the existing failure notes visible and do not describe every benchmark as passing.
