-- ops_health_check: dead tuple ratio for the tables with the most dead tuples
SELECT
    relname,
    n_live_tup,
    n_dead_tup,
    n_dead_tup::FLOAT / NULLIF(n_live_tup + n_dead_tup, 0) AS dead_ratio
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 20;
