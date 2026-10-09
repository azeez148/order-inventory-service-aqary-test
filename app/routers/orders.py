from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_read_session
from app.schemas import OrderCreate, OrderOut
from app.services import orders as svc

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, session: AsyncSession = Depends(get_session)):
    try:
        order = await svc.place_order(session, payload.items)
    except svc.ProductNotFound as e:
        raise HTTPException(404, f"Product {e.product_id} not found")
    except svc.InsufficientStock as e:
        raise HTTPException(409, f"Insufficient stock for product {e.product_id}")
    return order


@router.get("/{order_id}", response_model=OrderOut)
async def read_order(order_id: int, session: AsyncSession = Depends(get_read_session)):
    order = await svc.get_order(session, order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order
