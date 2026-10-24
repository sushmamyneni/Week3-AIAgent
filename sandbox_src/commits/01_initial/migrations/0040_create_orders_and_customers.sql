-- 0040: create the customers and orders tables
CREATE TABLE customers (
    id               BIGINT PRIMARY KEY,
    cust_email       VARCHAR(255),
    first_name       VARCHAR(100),
    phone            VARCHAR(20),
    country          CHAR(2),
    marketing_opt_in BOOLEAN DEFAULT FALSE
);

CREATE TABLE orders (
    id          BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers (id),
    total       NUMERIC(10, 2),
    created_at  TIMESTAMP
);

GRANT SELECT, INSERT, UPDATE ON customers, orders TO etl_user;
