"""Meilisearch integration (async httpx; no blocking SDK calls).

Meilisearch owns *relevance* (typo tolerance, prefix search, ranking).
Postgres stays the source of truth: search returns ids only, and the API
hydrates price/stock from Postgres so frequently-changing stock never needs
to be re-indexed.
"""
import asyncio
import logging

import httpx
from sqlalchemy import func, select, text

from app.config import settings
from app.db import OltpSession
from app.models import Product, SearchOutbox

log = logging.getLogger("search")
INDEX = "products"
# Serialize delivery and reindex snapshots across API processes. SKIP LOCKED
# alone could let two workers submit snapshots for the same product out of order.
DELIVERY_LOCK = 731904


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
        r = await self.http.get(f"/indexes/{INDEX}")
        if r.status_code == 404:
            r = await self.http.post("/indexes", json={"uid": INDEX, "primaryKey": "id"})
            await self.confirm(r)
        else:
            r.raise_for_status()
        r = await self.http.patch(
            f"/indexes/{INDEX}/settings",
            json={"searchableAttributes": ["name", "sku", "description"]},
        )
        await self.confirm(r)

    async def confirm(self, response: httpx.Response) -> None:
        """HTTP 202 only means accepted; acknowledge delivery after task success."""
        response.raise_for_status()
        task_id = response.json()["taskUid"]
        async with asyncio.timeout(30):
            while True:
                r = await self.http.get(f"/tasks/{task_id}")
                r.raise_for_status()
                task = r.json()
                if task["status"] == "succeeded":
                    return
                if task["status"] in {"failed", "canceled"}:
                    raise RuntimeError(f"Meilisearch task {task_id}: {task.get('error', task['status'])}")
                await asyncio.sleep(0.1)

    async def upsert(self, products: list[Product]) -> None:
        if not products:
            return
        r = await self.http.post(f"/indexes/{INDEX}/documents", json=[to_doc(p) for p in products])
        await self.confirm(r)

    async def delete(self, product_id: int) -> None:
        r = await self.http.delete(f"/indexes/{INDEX}/documents/{product_id}")
        await self.confirm(r)

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
        async with OltpSession() as s, s.begin():
            await s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DELIVERY_LOCK})
            rows = (
                await s.scalars(select(Product).where(Product.id > last_id).order_by(Product.id).limit(batch))
            ).all()
            if not rows:
                return total
            await client.upsert(list(rows))
            total += len(rows)
            last_id = rows[-1].id


async def drain_outbox(client: SearchClient, batch: int = 1000) -> int:
    async with OltpSession() as s, s.begin():
        locked = await s.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": DELIVERY_LOCK})
        if not locked:
            return 0
        events = (await s.scalars(
            select(SearchOutbox).where(SearchOutbox.processed_at.is_(None))
            .order_by(SearchOutbox.id).limit(batch).with_for_update(skip_locked=True)
        )).all()
        if not events:
            return 0
        products = (await s.scalars(
            select(Product).where(Product.id.in_({e.product_id for e in events}))
        )).all()
        await client.upsert(list(products))
        for event in events:
            event.processed_at = func.now()
        return len(events)


async def search_worker(client: SearchClient) -> None:
    """Retry forever; failures/cancellation roll back unacknowledged outbox rows."""
    initialized = False
    while True:
        try:
            if not initialized:
                await client.setup()
                await reindex_all(client)
                initialized = True
            if not await drain_outbox(client):
                await asyncio.sleep(0.5)
        except Exception:
            log.exception("Search delivery failed; pending outbox rows will be retried")
            await asyncio.sleep(1)
