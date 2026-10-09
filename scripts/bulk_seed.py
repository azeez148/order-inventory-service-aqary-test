"""Add an atomic bulk fixture without removing existing data.

docker compose run --rm api python -m scripts.bulk_seed
"""
import argparse
import asyncio
from pathlib import Path
import time
import uuid

from sqlalchemy import text

from app.db import oltp_engine


async def main(products: int, orders: int):
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    batch = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        async with oltp_engine.begin() as conn:
            result = (await conn.execute(text(sql), {
                "batch": batch, "products": products, "orders": orders,
            })).mappings().one()
            assert dict(result) == {
                "products": products, "orders": orders,
                "order_items": orders, "outbox_events": products,
            }, result
        print(f"Batch {batch}: {dict(result)} in {time.perf_counter() - start:.2f}s")
        print("Historical orders are fixtures; stock=1000 is remaining inventory after those sales.")
        print("Search indexing will converge through the transactional outbox.")
    finally:
        await oltp_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products", type=int, default=20_000)
    parser.add_argument("--orders", type=int, default=100_000)
    args = parser.parse_args()
    if args.products <= 0 or args.orders < 0:
        parser.error("products must be positive and orders nonnegative")
    asyncio.run(main(args.products, args.orders))
