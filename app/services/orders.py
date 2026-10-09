from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Order, OrderItem, Product
from app.schemas import OrderItemIn


class ProductNotFound(Exception):
    def __init__(self, product_id: int):
        self.product_id = product_id


class InsufficientStock(Exception):
    def __init__(self, product_id: int):
        self.product_id = product_id


async def place_order(session: AsyncSession, items: list[OrderItemIn]) -> Order:
    """Place an order atomically and race-safely.

    Stock is decremented with a single conditional UPDATE:
        UPDATE products SET stock = stock - :q WHERE id = :id AND stock >= :q
    Postgres takes a row lock, so concurrent orders for the same product queue
    up on that row; once the lock is released the WHERE is re-evaluated against
    the committed value, so two orders can never both consume the last unit.
    No read-then-write, no application-level locks.

    Deadlock avoidance: duplicate lines are merged and rows are always locked in
    ascending product-id order, so two multi-item orders can't lock in opposite order.
    Any failure rolls back the whole transaction (all-or-nothing).
    """
    wanted: dict[int, int] = defaultdict(int)
    for it in items:
        wanted[it.product_id] += it.quantity

    async with session.begin():
        total = Decimal("0")
        lines: list[tuple[int, int, Decimal]] = []
        for pid in sorted(wanted):
            qty = wanted[pid]
            row = (
                await session.execute(
                    update(Product)
                    .where(Product.id == pid, Product.stock >= qty)
                    .values(stock=Product.stock - qty)
                    .returning(Product.price)
                )
            ).first()
            if row is None:
                exists = await session.scalar(select(Product.id).where(Product.id == pid))
                raise (InsufficientStock(pid) if exists else ProductNotFound(pid))
            price = row[0]
            total += price * qty
            lines.append((pid, qty, price))

        order = Order(total=total, status="confirmed", items=[
            OrderItem(product_id=pid, quantity=q, unit_price=p) for pid, q, p in lines
        ])
        session.add(order)
        await session.flush()
    return order  # committed; expire_on_commit=False keeps response data available


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
