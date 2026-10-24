-- orders_load: copy the previous day's orders from staging into the warehouse
INSERT INTO orders (id, customer_id, customer_zip, total, created_at)
SELECT
    s.order_id,
    s.customer_id,
    s.customer_zip::INTEGER,       -- zip codes are numbers
    s.total,
    s.created_at
FROM staging.orders_raw AS s
WHERE s.created_at >= CURRENT_DATE - INTERVAL '1 day';
