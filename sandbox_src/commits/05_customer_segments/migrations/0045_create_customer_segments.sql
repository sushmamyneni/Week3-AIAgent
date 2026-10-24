-- 0045: customer segments for marketing
CREATE TABLE customer_segments (
    customer_id BIGINT PRIMARY KEY REFERENCES customers (id),
    segment     VARCHAR(30),
    updated_at  TIMESTAMP
);
-- TODO: grant access to the ETL role
