from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_reporting_session

router = APIRouter(prefix="/reports", tags=["reports"])

TOP_PRODUCTS_SQL = text("""
    SELECT p.id, p.sku, p.name,
           SUM(oi.quantity)                    AS units_sold,
           SUM(oi.quantity * oi.unit_price)    AS revenue,
           COUNT(DISTINCT oi.order_id)         AS order_count
    FROM order_items oi
    JOIN products p ON p.id = oi.product_id
    GROUP BY p.id, p.sku, p.name
    ORDER BY revenue DESC
    LIMIT :limit
""")

TOTALS_SQL = text("""
    SELECT (SELECT COUNT(*) FROM orders)                       AS orders,
           (SELECT COALESCE(SUM(total), 0) FROM orders)        AS revenue,
           (SELECT COUNT(*) FROM products WHERE stock < 10)    AS low_stock_products
""")


@router.get("/sales")
async def sales_report(
    limit: int = Query(10, ge=1, le=100),
    delay: float = Query(0, ge=0, le=10, description="Simulate extra-heavy work (pg_sleep seconds)"),
    session: AsyncSession = Depends(get_reporting_session),
):
    """Heavy aggregate across orders/products.

    Runs on the dedicated reporting pool, in a READ ONLY transaction with a
    statement timeout, so it can't block writers or hold connections forever.
    """
    async with session.begin():
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(text(f"SET LOCAL statement_timeout = {int(settings.reporting_statement_timeout_ms)}"))
        if delay:
            await session.execute(text("SELECT pg_sleep(CAST(:d AS double precision))"), {"d": delay})
        top = (await session.execute(TOP_PRODUCTS_SQL, {"limit": limit})).mappings().all()
        totals = (await session.execute(TOTALS_SQL)).mappings().one()
    return {
        "totals": {k: float(v) if k == "revenue" else int(v) for k, v in totals.items()},
        "top_products": [
            {**dict(r), "units_sold": int(r["units_sold"]), "revenue": float(r["revenue"])} for r in top
        ],
    }
