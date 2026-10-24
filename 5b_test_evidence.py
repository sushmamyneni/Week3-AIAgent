# Step 5b test: the evidence catalog and the citation check, on REAL tool output from the fixtures.
# No model calls. (One real docs search is made, so the first run loads the embedding model.)
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 5b_test_evidence.py
import evidence
import tools
from failure_source import FixtureFailureSource
from tickets import Evidence

source = FixtureFailureSource("incidents")
tools_by_name = {t.name: t for t in tools.build_tools(source)}

# Run real tools and build a state like the one the evidence loop leaves behind
calls = [
    ("search_code", {"query": "customer_zip INTEGER cast"}),                       # E1
    ("get_table_schema", {"table": "orders"}),                                      # E2
    ("git_history", {"path": "migrations/", "since": "2026-10-15"}),                # E3
    ("git_history", {"commit": "3cd3705"}),                                         # E4
    ("search_docs", {"query": "task killed out of memory SIGKILL", "system": "airflow"}),   # E5
    ("get_table_schema", {"table": "customer_segments"}),                           # E6
]
state = {"log_text": tools_by_name["get_failure_log"].invoke({"source_ref": "inc-08-column-type-changed"}),
         "evidence": [{"tool": n, "args": a, "ok": True, "result": tools_by_name[n].invoke(a)} for n, a in calls]}
state["evidence"].append({"tool": "search_code", "args": {"query": "x"}, "ok": False,
                          "result": "The tool failed: boom"})                       # E7, a failed call
catalog = evidence.build_catalog(state)

def citation(ref, quote):
    found, reason = evidence.check_quote(catalog, ref, quote)
    return found["citation"] if found else None

print("TEST 1: the catalog numbers the log and every tool call")
print("  ids:", list(catalog))
print("  kinds:", [catalog[i]["kind"] for i in catalog])
print("  the failed call is kept but marked:", catalog["E7"]["ok"] is False)

print("TEST 2: a code quote gets its file and line")
print("  ", citation("E1", "s.customer_zip::INTEGER"))
print("  correct:", citation("E1", "s.customer_zip::INTEGER") == "sql/orders_load.sql:6")
print("  extra spaces do not matter:", citation("E1", "s.customer_zip::INTEGER,       -- zip codes are numbers") == "sql/orders_load.sql:6")

print("TEST 3: a schema quote gets the table and the migration")
print("  ", citation("E2", "customer_zip INTEGER -> VARCHAR(10)"))
print("  change line:", citation("E2", "customer_zip INTEGER -> VARCHAR(10)") == "schema orders: migration 0044, 2026-10-20")
print("  current columns:", citation("E2", "customer_zip VARCHAR(10)") == "schema orders: current columns")
print("  privileges:", citation("E6", "etl_user: none") == "schema customer_segments: privileges")

print("TEST 4: a git quote gets the commit (and the file, for a diff line)")
print("  list:", citation("E3", "Store orders.customer_zip as text for international postal codes") == "commit 3cd3705")
print("  diff:", citation("E4", "ALTER COLUMN customer_zip TYPE VARCHAR(10)") ==
      "commit 3cd3705, migrations/0044_orders_customer_zip_to_varchar.sql")

print("TEST 5: a docs quote gets the page and heading, even when the text wraps over lines")
c = citation("E5", "it is killed with SIGKILL (exit code -9)")
print("  ", c)
print("  correct:", c is not None and c.startswith("airflow docs: troubleshooting.rst > "))

print("TEST 6: a log quote is found in the log")
print("  ", citation("L", "InvalidTextRepresentation"), "| correct:", citation("L", "InvalidTextRepresentation") == "task log")

print("TEST 7: a made-up or misplaced quote is refused, and the reason is given")
cases = {
    "invented text": ("E1", "the load casts the zip code to TEXT"),
    "real text, but from another evidence": ("E1", "etl_user: SELECT, INSERT, UPDATE"),
    "unknown evidence id": ("E99", "s.customer_zip::INTEGER"),
    "a failed tool call": ("E7", "The tool failed"),
    "too short": ("E1", "zip"),
    "two lines at once": ("E1", "s.customer_id,\n s.customer_zip::INTEGER"),
    "wrong case": ("E1", "S.CUSTOMER_ZIP::INTEGER"),
}
for name, (ref, quote) in cases.items():
    found, reason = evidence.check_quote(catalog, ref, quote)
    print(f"  {name}: refused = {found is None} | {reason}")

print("TEST 8: a verified quote fits the ticket's Evidence model")
found, _ = evidence.check_quote(catalog, "E1", "s.customer_zip::INTEGER")
print("  valid Evidence:", Evidence(**found).citation == "sql/orders_load.sql:6")

print("TEST 9: facts the action policy needs")
print("  line 6 text:", evidence.code_line(catalog, "sql/orders_load.sql", 6))
print("  a line never seen is None:", evidence.code_line(catalog, "sql/orders_load.sql", 99) is None)
print("  a file never seen is None:", evidence.code_line(catalog, "dags/other.py", 1) is None)
print("  owner from the search:", evidence.code_owner(catalog, "sql/orders_load.sql") == "@acme/orders-team")
print("  best docs score:", evidence.best_doc_score(catalog))
print("  no docs, no score:", evidence.best_doc_score({"L": catalog["L"]}) is None)

print("TEST 10: the catalog shown to the model carries the ids")
shown = evidence.render_catalog(catalog)
print("  every id appears:", all(f"=== {i}:" in shown for i in catalog))
print("  failed call flagged:", "[FAILED: cannot be cited]" in shown)