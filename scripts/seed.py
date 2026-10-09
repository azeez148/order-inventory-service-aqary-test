"""Seed demo data via the API from a CSV.
python scripts/seed.py --base-url http://localhost:8001 [--csv scripts/products.csv]
CSV columns: sku,name,description,price,stock
"""
import argparse
import asyncio
import csv
import random
from pathlib import Path

import httpx

DEFAULT_CSV = Path(__file__).with_name("products.csv")


def load_products(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            {
                "sku": row["sku"].strip(),
                "name": row["name"].strip(),
                "description": (row.get("description") or "").strip(),
                "price": row["price"].strip(),
                "stock": int(row["stock"]),
            }
            for row in csv.DictReader(f)
            if row.get("sku", "").strip()
        ]


async def main(base_url: str, csv_path: Path, n_orders: int) -> None:
    products = load_products(csv_path)
    async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
        ids = []
        for p in products:
            r = await c.post("/products", json=p)
            if r.status_code == 201:
                ids.append(r.json()["id"])
            elif r.status_code == 409:
                print(f"skip {p['sku']} (exists)")
            else:
                print(f"failed {p['sku']}: {r.status_code} {r.text}")
        print(f"created {len(ids)}/{len(products)} products from {csv_path}")

        if not ids or n_orders <= 0:
            return
        random.seed(42)
        ok = 0
        for _ in range(n_orders):
            items = [{"product_id": pid, "quantity": random.randint(1, 4)}
                     for pid in random.sample(ids, min(len(ids), random.randint(1, 3)))]
            ok += (await c.post("/orders", json={"items": items})).status_code == 201
        print(f"placed {ok}/{n_orders} orders")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--orders", type=int, default=40, help="random orders to place (0 to skip)")
    a = ap.parse_args()
    asyncio.run(main(a.base_url, a.csv, a.orders))