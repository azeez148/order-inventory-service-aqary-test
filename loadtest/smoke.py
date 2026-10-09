"""Focused runtime checks for search, multi-product transactions and pool errors."""
import asyncio
import time
import uuid

import httpx
from sqlalchemy.exc import TimeoutError as DatabasePoolTimeout

from app.db import get_read_session
from app.main import app


async def main():
    async with httpx.AsyncClient(base_url="http://api:8000", timeout=30) as c:
        async def product(stock, name=None):
            token = uuid.uuid4().hex
            r = await c.post("/products", json={"sku": token, "name": name or token,
                            "description": "smoke description", "price": "2.50", "stock": stock})
            r.raise_for_status()
            return r.json()

        async def stock(pid):
            r = await c.get(f"/products/{pid}")
            r.raise_for_status()
            return r.json()["stock"]

        p, q = await product(5), await product(5)
        payload = {"items": [{"product_id": q["id"], "quantity": 1},
                             {"product_id": p["id"], "quantity": 1}]}
        results = await asyncio.gather(*(c.post("/orders", json=payload) for _ in range(20)))
        assert sum(r.status_code == 201 for r in results) == 5
        assert sum(r.status_code == 409 for r in results) == 15
        assert await stock(p["id"]) == await stock(q["id"]) == 0
        order = next(r.json() for r in results if r.status_code == 201)
        assert len(order["items"]) == 2 and float(order["total"]) == 5
        detail = await c.get(f"/orders/{order['id']}")
        assert detail.status_code == 200 and detail.json() == order
        print("PASS: concurrent multi-product orders and detail retrieval")

        a, b = await product(3), await product(0)
        failed = await c.post("/orders", json={"items": [
            {"product_id": a["id"], "quantity": 1}, {"product_id": b["id"], "quantity": 1}]})
        assert failed.status_code == 409 and await stock(a["id"]) == 3
        duplicate = await c.post("/orders", json={"items": [
            {"product_id": a["id"], "quantity": 1}, {"product_id": a["id"], "quantity": 2}]})
        assert duplicate.status_code == 201 and await stock(a["id"]) == 0
        assert len(duplicate.json()["items"]) == 1 and duplicate.json()["items"][0]["quantity"] == 3
        print("PASS: all-or-nothing rollback and duplicate-line merging")

        async def find(term, pid):
            for _ in range(50):
                t = time.perf_counter()
                r = await c.get("/products/search", params={"q": term})
                r.raise_for_status()
                elapsed = (time.perf_counter() - t) * 1000
                if any(hit["id"] == pid for hit in r.json()):
                    return elapsed, r.json()
                await asyncio.sleep(0.1)
            raise AssertionError("search index did not converge within 5 seconds")

        s = await product(4)
        elapsed, _ = await find(s["name"], s["id"])
        description = uuid.uuid4().hex
        updated = await c.patch(f"/products/{s['id']}", json={"description": description, "price": "3.25"})
        updated.raise_for_status()
        _, hits = await find(description, s["id"])
        hit = next(h for h in hits if h["id"] == s["id"])
        assert float(hit["price"]) == 3.25 and hit["stock"] == 4
        assert elapsed < 1000
        print(f"PASS: name/description search, update synchronization, fresh price/stock ({elapsed:.1f}ms lookup)")

    async def exhausted():
        raise DatabasePoolTimeout("simulated checkout exhaustion")
        yield

    app.dependency_overrides[get_read_session] = exhausted
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/products/1")
            assert r.status_code == 503 and r.headers["Retry-After"] == "1"
        print("PASS: SQLAlchemy pool exhaustion returns 503 with Retry-After")
    finally:
        app.dependency_overrides.clear()


if __name__ == "__main__":
    asyncio.run(main())
