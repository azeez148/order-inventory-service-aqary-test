import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as DatabasePoolTimeout
from sqlalchemy import text

from app.db import oltp_engine, reporting_engine, read_engine
from app.models import Base
from app.routers import orders, products, reports
from app.search import SearchClient, search_worker

log = logging.getLogger("app")


async def _init_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            async with oltp_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as e:  # DB/PgBouncer may still be starting
            log.warning("DB not ready (%s), retry %s/%s", e.__class__.__name__, attempt + 1, retries)
            await asyncio.sleep(1)
    raise RuntimeError("Database unavailable")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    app.state.search = SearchClient()
    task = asyncio.create_task(search_worker(app.state.search))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await app.state.search.close()
        await oltp_engine.dispose()
        await reporting_engine.dispose()
        await read_engine.dispose()


app = FastAPI(title="Order & Inventory Service", version="0.1.0", lifespan=lifespan)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(reports.router)


@app.get("/health", tags=["meta"])
async def health():
    async with oltp_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}

@app.exception_handler(DatabasePoolTimeout)
async def pool_exhausted(_: Request, __: DatabasePoolTimeout):
    return JSONResponse({"detail": "Database busy, retry shortly"}, status_code=503, headers={"Retry-After": "1"})
