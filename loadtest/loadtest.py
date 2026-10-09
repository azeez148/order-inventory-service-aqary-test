"""Concurrency load test: orders + heavy reports fired at the same time.

Phase A (baseline): N orders for one hot product (stock S < N), plus reads/search.
Phase B (contended): same, but while R slow reports run, plus read/search probes.

Asserts: successes == S, rejections == N - S, final stock == 0 (no overselling),
and prints latency percentiles so responsiveness under reporting load is visible.

Run:  docker compose run --rm api python loadtest/loadtest.py --base-url http://api:8000
"""
import argparse
import asyncio
from collections import Counter
import sys
import time
import uuid

import httpx


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p / 100))] * 1000 if s else 0.0


def summary(name: str, lat: list[float]) -> str:
    return f"  {name:<16} n={len(lat):<5} p50={pct(lat, 50):7.1f}ms  p95={pct(lat, 95):7.1f}ms  max={pct(lat, 100):7.1f}ms"


async def make_product(c: httpx.AsyncClient, stock: int) -> int:
    r = await c.post("/products", json={
        "sku": f"LT-{uuid.uuid4().hex[:10]}", "name": "Load test widget",
        "description": "concurrency test", "price": "9.99", "stock": stock})
    r.raise_for_status()
    return r.json()["id"]


async def timed(c: httpx.AsyncClient, method: str, url: str, expected_product_id=None, **kw):
    t = time.perf_counter()
    try:
        r = await c.request(method, url, **kw)
        if r.status_code == 200 and expected_product_id is not None:
            hits = r.json()
            if not any(p["id"] == expected_product_id and p["stock"] >= 0
                       and p["price"] == "9.99" for p in hits):
                return "BAD_SEARCH_RESULT", time.perf_counter() - t
        return r.status_code, time.perf_counter() - t
    except httpx.HTTPError as e:
        return f"ERR:{type(e).__name__}", time.perf_counter() - t


async def run_phase(c, report_client, read_client, search_client, label, n_orders, stock, n_reports,
                    report_delay, probes, searches, order_p95_ms, read_p95_ms, search_p95_ms, concurrency):
    pid = await make_product(c, stock)
    product = await c.get(f"/products/{pid}")
    product.raise_for_status()
    term = product.json()["sku"]
    # Eventual index convergence is setup, outside latency measurement. A 200
    # with no expected hit is not evidence of successful search under load.
    if searches:
        async with asyncio.timeout(30):
            while True:
                r = await search_client.get("/products/search", params={"q": term})
                if r.status_code == 200 and any(p["id"] == pid for p in r.json()):
                    break
                await asyncio.sleep(0.1)
    sem = asyncio.Semaphore(concurrency)
    queued_latencies = []

    async def order():
        submitted = time.perf_counter()
        async with sem:
            result = await timed(c, "POST", "/orders", json={"items": [{"product_id": pid, "quantity": 1}]})
        queued_latencies.append(time.perf_counter() - submitted)
        return result

    async def report():
        return await timed(report_client, "GET", "/reports/sales", params={"delay": report_delay})

    async def probe():
        return await timed(read_client, "GET", f"/products/{pid}")

    async def search():
        return await timed(search_client, "GET", "/products/search",
                           expected_product_id=pid, params={"q": term})

    t0 = time.perf_counter()
    # Reports are launched first so they are in flight while orders hammer the same DB.
    report_tasks = [asyncio.create_task(report()) for _ in range(n_reports)]
    await asyncio.sleep(0.2)
    order_tasks = [asyncio.create_task(order()) for _ in range(n_orders)]
    probe_tasks = [asyncio.create_task(probe()) for _ in range(probes)]
    search_tasks = [asyncio.create_task(search()) for _ in range(searches)]
    orders = await asyncio.gather(*order_tasks)
    probe_res = await asyncio.gather(*probe_tasks)
    search_res = await asyncio.gather(*search_tasks)
    reports = await asyncio.gather(*report_tasks)
    wall = time.perf_counter() - t0

    ok = sum(1 for s, _ in orders if s == 201)
    conflict = sum(1 for s, _ in orders if s == 409)
    other = n_orders - ok - conflict
    final_stock = (await c.get(f"/products/{pid}")).json()["stock"]

    print(f"\n[{label}] wall={wall:.2f}s")
    print(summary("POST /orders", [d for _, d in orders]))
    print(summary("orders incl queue", queued_latencies))
    if reports:
        print(summary("GET /reports", [d for _, d in reports]))
        print(f"  report statuses: {dict(Counter(s for s, _ in reports))}")
    if probe_res:
        print(summary("GET /products/id", [d for _, d in probe_res]))
        print(f"  probe statuses: {dict(Counter(s for s, _ in probe_res))}")
    if search_res:
        print(summary("GET /search", [d for _, d in search_res]))
        print(f"  search statuses: {dict(Counter(s for s, _ in search_res))}")
    print(f"  order statuses: {dict(Counter(s for s, _ in orders))}")

    passed = ok == min(stock, n_orders) and other == 0 and final_stock == max(stock - n_orders, 0) and final_stock >= 0
    print(f"  {'PASS' if passed else 'FAIL'}: no overselling" if passed else "  FAIL: stock invariant violated")
    statuses_ok = all(s == 200 for s, _ in reports + probe_res + search_res)
    order_p95 = pct([d for _, d in orders], 95)
    responsive = (order_p95 <= order_p95_ms
                  and pct([d for _, d in probe_res], 95) <= read_p95_ms
                  and pct([d for _, d in search_res], 95) <= search_p95_ms)
    print(f"  {'PASS' if statuses_ok else 'FAIL'}: report/read/search statuses and search results")
    print(f"  {'PASS' if responsive else 'FAIL'}: latency targets (orders p95 <= {order_p95_ms:g}ms, reads p95 <= {read_p95_ms:g}ms, search p95 <= {search_p95_ms:g}ms)")
    return passed and statuses_ok and responsive, order_p95


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--orders", type=int, default=500)
    ap.add_argument("--stock", type=int, default=100)
    ap.add_argument("--reports", type=int, default=5)
    ap.add_argument("--report-delay", type=float, default=2.0)
    ap.add_argument("--probes", type=int, default=100)
    ap.add_argument("--searches", type=int, default=50)
    ap.add_argument("--order-p95-ms", type=float, default=5000)
    ap.add_argument("--read-p95-ms", type=float, default=2000)
    ap.add_argument("--search-p95-ms", type=float, default=2000)
    ap.add_argument("--max-slowdown", type=float, default=2.0)
    ap.add_argument("--concurrency", type=int, default=50,
                    help="maximum in-flight orders; total order count is unchanged")
    ap.add_argument("--cold", action="store_true", help="skip explicit database/HTTP pool warm-up")
    a = ap.parse_args()
    if min(a.orders, a.probes, a.searches, a.reports, a.stock) < 0 or a.orders == 0 or a.concurrency <= 0:
        ap.error("orders must be positive; stock, reports, probes and searches must be nonnegative")
    if not 0 <= a.report_delay <= 10 or min(a.order_p95_ms, a.read_p95_ms, a.search_p95_ms, a.max_slowdown) <= 0:
        ap.error("delay must be 0..10; latency targets and max-slowdown must be positive")

    limits = httpx.Limits(max_connections=300, max_keepalive_connections=100)
    # Independent tools have independent HTTP pools: a reporting request must
    # not consume a storefront client's connection slot in the load generator.
    async with (httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=limits) as c,
                httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=limits) as report_client,
                httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=limits) as read_client,
                httpx.AsyncClient(base_url=a.base_url, timeout=60, limits=limits) as search_client):
        if not a.cold:
            pid = await make_product(c, 0)
            warmup = await asyncio.gather(
                *(c.get("/health") for _ in range(20)),
                *(read_client.get(f"/products/{pid}") for _ in range(10)),
                *(report_client.get("/reports/sales") for _ in range(3)),
            )
            for response in warmup:
                response.raise_for_status()
            print(f"Warm-up complete; total orders={a.orders}, in-flight orders={a.concurrency}")
        a_ok, baseline = await run_phase(c, report_client, read_client, search_client, "A: orders + reads + search",
                                        a.orders, a.stock, 0, 0, a.probes, a.searches,
                                        a.order_p95_ms, a.read_p95_ms, a.search_p95_ms, a.concurrency)
        b_ok, mixed = await run_phase(c, report_client, read_client, search_client, "B: orders + reads + search + reports",
                                     a.orders, a.stock, a.reports, a.report_delay, a.probes, a.searches,
                                     a.order_p95_ms, a.read_p95_ms, a.search_p95_ms, a.concurrency)
    ratio = mixed / baseline if baseline else float("inf")
    isolation_ok = ratio <= a.max_slowdown
    print(f"\n{'PASS' if isolation_ok else 'FAIL'}: mixed/baseline order p95 = {ratio:.2f}x (target <= {a.max_slowdown:g}x)")
    return 0 if a_ok and b_ok and isolation_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
