-- marketing_export: customers who opted in to marketing, for the weekly email campaign
SELECT
    c.id,
    c.cust_email,
    c.first_name,
    c.country
FROM customers AS c
WHERE c.marketing_opt_in = TRUE;
