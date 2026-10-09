"""Focused outbox proof. Run with the API stopped so only this consumer drains.

docker compose stop api
docker compose run --rm --no-deps api python -m loadtest.outbox
docker compose start api
"""
import asyncio
import uuid

import httpx
from sqlalchemy import select

from app.db import OltpSession, oltp_engine, read_engine, reporting_engine
from app.models import Product, SearchOutbox
from app.search import SearchClient, drain_outbox


async def main():
    token = uuid.uuid4().hex
    async with OltpSession() as s:
        async with s.begin():
            p = Product(sku=token, name=token, description="outbox proof", price=1, stock=1)
            s.add(p)
            await s.flush()
            pid = p.id
            s.add(SearchOutbox(product_id=pid))
        # A failed product transaction must not leave either a change or an event.
        try:
            async with s.begin():
                p.name = "rolled back"
                s.add(SearchOutbox(product_id=pid))
                await s.flush()
                raise RuntimeError("rollback probe")
        except RuntimeError:
            pass
    async with OltpSession() as s:
        assert (await s.get(Product, pid)).name == token
        events = (await s.scalars(select(SearchOutbox).where(SearchOutbox.product_id == pid))).all()
        assert len(events) == 1 and events[0].processed_at is None
    print("PASS: product and outbox commit/rollback together")

    calls = []

    def failed_task(request):
        calls.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(202, json={"taskUid": 42})
        return httpx.Response(200, json={"status": "failed", "error": {"message": "proof failure"}})

    client = SearchClient()
    await client.close()
    client.http = httpx.AsyncClient(base_url="http://mock", transport=httpx.MockTransport(failed_task))
    try:
        try:
            await drain_outbox(client)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Failed indexing task was acknowledged")
        assert "/tasks/42" in calls
        async with OltpSession() as s:
            event = await s.scalar(select(SearchOutbox).where(SearchOutbox.product_id == pid))
            assert event.processed_at is None
        print("PASS: HTTP acceptance followed by task failure leaves event pending")
    finally:
        await client.close()

    client = SearchClient()
    try:
        await client.setup()
        while await drain_outbox(client):
            pass
        async with OltpSession() as s:
            event = await s.scalar(select(SearchOutbox).where(SearchOutbox.product_id == pid))
            assert event.processed_at is not None
        assert pid in await client.search_ids(token, 20, 0)
        print("PASS: retry confirms real indexing success before acknowledging event")
    finally:
        await client.close()
        await oltp_engine.dispose()
        await read_engine.dispose()
        await reporting_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
