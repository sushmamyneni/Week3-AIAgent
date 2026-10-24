-- 0044: the web shop now sells in Canada and the UK, so postal codes are not always numbers
ALTER TABLE orders ALTER COLUMN customer_zip TYPE VARCHAR(10);
