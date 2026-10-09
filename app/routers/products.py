from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_read_session
from app.models import Product, SearchOutbox
from app.schemas import ProductCreate, ProductOut, ProductUpdate, StockAdjustment
from app.search import SearchClient, reindex_all

router = APIRouter(tags=["products"])


def get_search(request: Request) -> SearchClient:
    return request.app.state.search


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    session: AsyncSession = Depends(get_session),
):
    product = Product(**payload.model_dump())
    session.add(product)
    try:
        await session.flush()
        session.add(SearchOutbox(product_id=product.id))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "SKU already exists")
    await session.refresh(product)
    return product


# Declared before /products/{id} so "search" isn't parsed as an id.
@router.get("/products/search", response_model=list[ProductOut])
async def search_products(
    q: str = Query(min_length=1),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_read_session),
    search: SearchClient = Depends(get_search),
):
    try:
        ids = await search.search_ids(q, limit, offset)
    except Exception:
        raise HTTPException(503, "Search backend unavailable")
    if not ids:
        return []
    rows = (await session.scalars(select(Product).where(Product.id.in_(ids)))).all()  # fresh price/stock
    by_id = {p.id: p for p in rows}
    return [by_id[i] for i in ids if i in by_id]  # keep Meilisearch relevance order


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, session: AsyncSession = Depends(get_read_session)):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    session: AsyncSession = Depends(get_session),
):
    product = await session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(product, k, v)
    session.add(SearchOutbox(product_id=product.id))
    await session.commit()
    await session.refresh(product)
    return product


@router.post("/products/{product_id}/stock-adjustments", response_model=ProductOut)
async def adjust_stock(
    product_id: int, payload: StockAdjustment, session: AsyncSession = Depends(get_session)
):
    """Warehouse stock update: atomic `stock = stock + delta`, never below zero."""
    row = (
        await session.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock + payload.delta >= 0)
            .values(stock=Product.stock + payload.delta)
            .returning(Product)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        exists = await session.get(Product, product_id)
        raise HTTPException(404 if exists is None else 409, "Product not found" if exists is None else "Stock would go negative")
    session.add(SearchOutbox(product_id=product_id))
    await session.commit()
    return row


@router.post("/admin/reindex", tags=["admin"])
async def reindex(search: SearchClient = Depends(get_search)):
    """Rebuild the search index from Postgres (repairs any drift)."""
    return {"indexed": await reindex_all(search)}
