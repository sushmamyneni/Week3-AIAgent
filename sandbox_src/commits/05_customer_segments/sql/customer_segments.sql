-- customer_segments_refresh: recompute each customer's segment from the last 90 days of orders
INSERT INTO customer_segments (customer_id, segment, updated_at)
SELECT
    o.customer_id,
    CASE WHEN SUM(o.total) > 1000 THEN 'high_value' ELSE 'standard' END,
    NOW()
FROM orders AS o
WHERE o.created_at >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY o.customer_id
ON CONFLICT (customer_id) DO UPDATE
    SET segment = EXCLUDED.segment, updated_at = EXCLUDED.updated_at;
