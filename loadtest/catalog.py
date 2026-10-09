"""Validate one bulk fixture, report totals and confirmed catalog indexing."""
import argparse
import asyncio
from decimal import Decimal

import httpx
from sqlalchemy import text

from app.db import ReadSession, oltp_engine, read_engine, reporting_engine
from app.search import SearchClient


async def main(products: int, orders: int):
    try:
        async with ReadSession() as s:
            counts = (await s.execute(text("""
                SELECT (SELECT count(*) FROM products WHERE sku LIKE 'BULK-%') AS products,
                       (SELECT count(*) FROM order_items oi JOIN products p ON p.id=oi.product_id
                        WHERE p.sku LIKE 'BULK-%') AS items,
                       (SELECT count(DISTINCT oi.order_id) FROM order_items oi
                        JOIN products p ON p.id=oi.product_id WHERE p.sku LIKE 'BULK-%') AS orders,
                       (SELECT count(*) FROM products WHERE stock < 0) AS negative_stock,
                       (SELECT sum(total) FROM orders) AS revenue,
                       (SELECT sum(quantity * unit_price) FROM order_items) AS item_revenue,
                       (SELECT count(*) FROM orders) AS total_orders,
                       (SELECT count(*) FROM products) AS total_products
            """))).mappings().one()
            assert counts["products"] == products and counts["orders"] == counts["items"] == orders, counts
            assert counts["negative_stock"] == 0 and counts["revenue"] == counts["item_revenue"], counts
            sample = (await s.execute(text(
                "SELECT id, sku FROM products WHERE sku LIKE 'BULK-%' ORDER BY id DESC LIMIT 1"
            ))).mappings().one()
        print(f"PASS: {products} bulk products, {orders} consistent orders/items, nonnegative stock")
        async with httpx.AsyncClient(base_url="http://api:8000", timeout=40) as c:
            r = await c.get("/reports/sales")
            r.raise_for_status()
            totals = r.json()["totals"]
            assert totals["orders"] == counts["total_orders"]
            assert Decimal(str(totals["revenue"])).quantize(Decimal("0.01")) == counts["revenue"]
            print("PASS: real sales aggregation agrees with database totals (no simulated delay)")
            async with asyncio.timeout(180):
                while True:
                    async with ReadSession() as s:
                        pending = await s.scalar(text("""
                            SELECT count(*) FROM search_outbox o JOIN products p ON p.id=o.product_id
                            WHERE p.sku LIKE 'BULK-%' AND o.processed_at IS NULL
                        """))
                    if pending == 0:
                        break
                    await asyncio.sleep(0.5)
            r = await c.get("/products/search", params={"q": sample["sku"]})
            r.raise_for_status()
            assert any(p["id"] == sample["id"] for p in r.json())
        search = SearchClient()
        try:
            r = await search.http.get("/indexes/products/stats")
            r.raise_for_status()
            assert r.json()["numberOfDocuments"] == counts["total_products"], r.json()
        finally:
            await search.close()
        print("PASS: all bulk outbox events confirmed; index document count matches database")
    finally:
        await oltp_engine.dispose()
        await read_engine.dispose()
        await reporting_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=20_000)
    parser.add_argument("--orders", type=int, default=100_000)
    args = parser.parse_args()
    asyncio.run(main(args.products, args.orders))
