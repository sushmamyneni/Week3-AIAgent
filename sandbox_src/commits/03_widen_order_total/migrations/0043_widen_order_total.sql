-- 0043: large wholesale orders overflowed NUMERIC(10,2)
ALTER TABLE orders ALTER COLUMN total TYPE NUMERIC(12, 2);
