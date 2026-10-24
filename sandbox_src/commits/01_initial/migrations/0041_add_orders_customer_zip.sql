-- 0041: keep the customer's zip code on the order
ALTER TABLE orders ADD COLUMN customer_zip INTEGER;
