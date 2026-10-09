"""Meilisearch integration (async httpx; no blocking SDK calls).

Meilisearch owns *relevance* (typo tolerance, prefix search, ranking).
Postgres stays the source of truth: search returns ids only, and the API
hydrates price/stock from Postgres so frequently-changing stock never needs
to be re-indexed.
"""
import logging

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import OltpSession
from app.models import Product

log = logging.getLogger("search")
INDEX = "products"


def to_doc(p: Product) -> dict:
    return {"id": p.id, "sku": p.sku, "name": p.name, "description": p.description, "price": float(p.price)}


class SearchClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            base_url=settings.meili_url,
            headers={"Authorization": f"Bearer {settings.meili_master_key}"},
            timeout=5.0,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def setup(self) -> None:
        await self.http.post("/indexes", json={"uid": INDEX, "primaryKey": "id"})
        r = await self.http.patch(
            f"/indexes/{INDEX}/settings",
            json={"searchableAttributes": ["name", "sku", "description"]},
        )
        r.raise_for_status()

    async def upsert(self, products: list[Product]) -> None:
        if not products:
            return
        r = await self.http.post(f"/indexes/{INDEX}/documents", json=[to_doc(p) for p in products])
        r.raise_for_status()

    async def delete(self, product_id: int) -> None:
        await self.http.delete(f"/indexes/{INDEX}/documents/{product_id}")

    async def search_ids(self, q: str, limit: int, offset: int) -> list[int]:
        r = await self.http.post(
            f"/indexes/{INDEX}/search",
            json={"q": q, "limit": limit, "offset": offset, "attributesToRetrieve": ["id"]},
        )
        r.raise_for_status()
        return [h["id"] for h in r.json()["hits"]]


async def reindex_all(client: SearchClient, batch: int = 1000) -> int:
    """Rebuild the index from Postgres (keyset pagination). Used on startup and by /admin/reindex."""
    total, last_id = 0, 0
    while True:
        async with OltpSession() as s:
            rows = (
                await s.scalars(select(Product).where(Product.id > last_id).order_by(Product.id).limit(batch))
            ).all()
        if not rows:
            return total
        await client.upsert(list(rows))
        total += len(rows)
        last_id = rows[-1].id


async def safe_upsert(client: SearchClient, product: Product) -> None:
    """Background-task wrapper: never fail the request because search is down."""
    try:
        await client.upsert([product])
    except Exception:
        log.exception("search upsert failed for product %s; /admin/reindex will repair", product.id)
