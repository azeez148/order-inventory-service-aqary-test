from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://shop:shop@pgbouncer:5432/shop"
    reporting_database_url: str = "postgresql+asyncpg://shop:shop@pgbouncer:5432/shop_reporting"
    read_database_url: str = "postgresql+asyncpg://shop:shop@pgbouncer:5432/shop_reads"
    read_pool_size: int = 10

    # App-side pools (client side of PgBouncer). Kept small: PgBouncer does the real pooling.
    oltp_pool_size: int = 15
    oltp_max_overflow: int = 5
    reporting_pool_size: int = 3
    reporting_max_overflow: int = 0
    pool_timeout: float = 10.0
    reporting_statement_timeout_ms: int = 30_000
    reporting_pool_timeout: float = 60.0

    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "dev-master-key"


settings = Settings()
