# Step 3c test: the code index finds the right files and lines in the sandbox repository.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 3c_test_code_index.py
import yaml

import code_index
from code_index import CodeIndex

# The repository folder and chunk size come from config.yaml (tools.code_repo_dir, tools.code_max_chunk_lines)
cfg = yaml.safe_load(open("config.yaml"))
index = CodeIndex(cfg["tools"]["code_repo_dir"], max_lines=cfg["tools"]["code_max_chunk_lines"])

print(f"TEST 1: the repository is indexed: {len(index.chunks)} chunks from "
      f"{len({c['path'] for c in index.chunks})} files")
print("  .git is never indexed:", not any(c["path"].startswith(".git") for c in index.chunks))
print("  no absolute paths:", not any(":" in c["path"] or c["path"].startswith("/") for c in index.chunks))

# Print the top hits of a query and return them
def show(query, top_k=3):
    print(f"\n  search: {query!r}")
    hits = index.search(query, top_k=top_k)
    for h in hits:
        lines = ", ".join(f"{n}: {t.strip()[:48]}" for n, t in h["match_lines"][:2])
        print(f"    {h['path']}:{h['start']}-{h['end']}  owner={h['owner']}  exact={h['exact']}  lines [{lines}]")
    return hits

# True if a hit for this file reports a match on this line number
def has_line(hits, path, line):
    return any(x["path"] == path and any(n == line for n, _ in x["match_lines"]) for x in hits)

print("\nTEST 2: identifiers from the failures find the right code (inc-08, inc-09, inc-10)")
h = show("customer_zip INTEGER cast")
print("  orders_load.sql line 6 found:", has_line(h, "sql/orders_load.sql", 6))
h = show("cust_email")
print("  marketing_export.sql line 4 found:", has_line(h, "sql/marketing_export.sql", 4))
h = show("grant access to the ETL role customer_segments")
print("  migration 0045 found:", any(x["path"].startswith("migrations/0045") for x in h))

print("\nTEST 3: configuration lines for the docs-based incidents (inc-01, inc-02, inc-03, inc-05, inc-06)")
h = show("worker_memory_limit inventory_refresh")
print("  dags/resources.yaml line 3 found:", has_line(h, "dags/resources.yaml", 3))
h = show("retries daily_sales_report")
print("  daily_sales_report.py line 11 found:", has_line(h, "dags/daily_sales_report.py", 11))
h = show("sensor timeout supplier_feed_ingest")
print("  supplier_feed_ingest.py line 22 found:", has_line(h, "dags/supplier_feed_ingest.py", 22))
h = show("spark.sql.shuffle.partitions sessionize_events")
print("  sessionize_events.conf line 5 found:", has_line(h, "spark_jobs/conf/sessionize_events.conf", 5))

print("\nTEST 4: owners come from CODEOWNERS")
expected = {
    "sql/orders_load.sql": "@acme/orders-team",
    "sql/marketing_export.sql": "@acme/marketing-data",
    "migrations/0045_create_customer_segments.sql": "@acme/data-platform",
    "spark_jobs/conf/daily_aggregates.conf": "@acme/analytics-engineering",
    "dags/resources.yaml": "@acme/data-platform",
}
for path, owner in expected.items():
    got = code_index.owner_of(path, index.rules)
    print(f"  {path}: {got} (expected {owner}) -> {got == owner}")

print("\nTEST 5: long files are split by function and by statement")
py_lines = ["import os", "", "def a():", "    return 1", "", "@decorator", "def b():", "    x = 1", "    return x", "",
            "class C:", "    def m(self):", "        return 2", "", "VALUE = 3"]
for c in code_index.split_python("big.py", py_lines):
    print(f"  python {c['start']}-{c['end']}: {c['kind']}")
sql_lines = ["-- first", "SELECT 1;", "", "-- second", "SELECT 2", "FROM t;", "", "SELECT 3;  -- last"]
for c in code_index.split_sql("big.sql", sql_lines):
    print(f"  sql {c['start']}-{c['end']}: {c['kind']}")
