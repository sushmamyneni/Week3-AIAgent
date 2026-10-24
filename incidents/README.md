# Incident fixtures (synthetic)

Twelve made-up failures for testing the failure-triage agent. Everything here is invented: the companies,
hosts, tables, passwords and tokens are fake, and the logs were written by hand in the style of real Airflow,
Spark and PostgreSQL output.

Each folder has:
- `event.json`: the structured failure event the watcher would receive. Some events are deliberately thin
  (only a DAG name), to test intake.
- `task.log`: the failure log. It includes noise, decoys, and a few fake secrets that must be redacted.
- `expected.json`: the answer key (root cause keywords, expected action, expected evidence, owner, and the
  traps in the incident). Used only to evaluate the agent. It must never be shown to the agent.

| # | Folder | Kind | Expected action |
|---|---|---|---|
| 1 | inc-01-airflow-oom | docs | config_change |
| 2 | inc-02-airflow-no-retries | docs | config_change |
| 3 | inc-03-airflow-sensor-timeout | docs | config_change |
| 4 | inc-04-airflow-queued-timeout | docs | config_change |
| 5 | inc-05-spark-executor-overhead | docs | config_change |
| 6 | inc-06-spark-shuffle-partitions | docs | config_change |
| 7 | inc-07-spark-lost-node | docs | retry |
| 8 | inc-08-column-type-changed | enterprise | code_change |
| 9 | inc-09-column-renamed | enterprise | code_change |
| 10 | inc-10-missing-grant | enterprise | code_change |
| 11 | inc-11-snowflake-quota | out_of_scope | escalate |
| 12 | inc-12-postgres-autovacuum | out_of_scope | escalate |
