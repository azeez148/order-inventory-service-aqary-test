"""Separate write, storefront-read and reporting pools through PgBouncer.

* OLTP engine       -> alias `shop`            (product writes, orders, stock)
* Read engine       -> alias `shop_reads`      (storefront reads, order retrieval)
* Reporting engine  -> alias `shop_reporting`  (heavy reads, separate pool + timeout)

A slow report cannot consume write/read pool slots. CPU, I/O and database
locks are still shared, so this is connection isolation, not total isolation.
"""
from collections.abc import AsyncIterator
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# PgBouncer in transaction mode hands a different server connection to each
# transaction, so asyncpg's per-connection prepared statements must be disabled.
_PGBOUNCER_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
}

oltp_engine = create_async_engine(
    settings.database_url,
    pool_size=settings.oltp_pool_size,
    max_overflow=settings.oltp_max_overflow,
    pool_timeout=settings.pool_timeout,
    connect_args=_PGBOUNCER_ARGS,
)
reporting_engine = create_async_engine(
    settings.reporting_database_url,
    pool_size=settings.reporting_pool_size,
    max_overflow=settings.reporting_max_overflow,
    pool_timeout=settings.reporting_pool_timeout,
    connect_args=_PGBOUNCER_ARGS,
)

OltpSession = async_sessionmaker(oltp_engine, expire_on_commit=False)
ReportingSession = async_sessionmaker(reporting_engine, expire_on_commit=False)
read_engine = create_async_engine(
    settings.read_database_url,
    pool_size=settings.read_pool_size,
    max_overflow=0,
    pool_timeout=settings.pool_timeout,
    connect_args=_PGBOUNCER_ARGS,
)
ReadSession = async_sessionmaker(read_engine, expire_on_commit=False)


async def get_read_session() -> AsyncIterator[AsyncSession]:
    async with ReadSession() as session:
        yield session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with OltpSession() as session:
        yield session


async def get_reporting_session() -> AsyncIterator[AsyncSession]:
    async with ReportingSession() as session:
        yield session
