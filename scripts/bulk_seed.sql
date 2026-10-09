-- One atomic fixture: historical sales plus their remaining inventory.
-- Sequence allocation avoids assuming an empty database or contiguous IDs.
WITH inserted_products AS (
    INSERT INTO products (sku, name, description, price, stock)
    SELECT 'BULK-' || :batch || '-' || g,
           'Catalog product ' || g,
           'Bulk fixture catalog item ' || g,
           (1 + (g % 10000) / 100.0)::numeric(12, 2),
           1000
    FROM generate_series(1, CAST(:products AS integer)) g
    RETURNING id, price
), numbered_products AS (
    SELECT id, price, row_number() OVER (ORDER BY id) AS n
    FROM inserted_products
), order_rows AS MATERIALIZED (
    SELECT nextval(pg_get_serial_sequence('orders', 'id')) AS id,
           p.id AS product_id, p.price,
           1 + (g % 3) AS quantity,
           now() - (g % 365) * interval '1 day' AS created_at
    FROM generate_series(1, CAST(:orders AS integer)) g
    JOIN numbered_products p ON p.n = 1 + ((g - 1) % CAST(:products AS integer))
), inserted_orders AS (
    INSERT INTO orders (id, status, total, created_at)
    SELECT id, 'confirmed', quantity * price, created_at FROM order_rows
    RETURNING id
), inserted_items AS (
    INSERT INTO order_items (order_id, product_id, quantity, unit_price)
    SELECT r.id, r.product_id, r.quantity, r.price
    FROM order_rows r JOIN inserted_orders o ON o.id = r.id
    RETURNING id
), inserted_events AS (
    INSERT INTO search_outbox (product_id)
    SELECT id FROM inserted_products
    RETURNING id
)
SELECT (SELECT count(*) FROM inserted_products) AS products,
       (SELECT count(*) FROM inserted_orders) AS orders,
       (SELECT count(*) FROM inserted_items) AS order_items,
       (SELECT count(*) FROM inserted_events) AS outbox_events
