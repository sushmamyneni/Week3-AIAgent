# Step 3b test: search_docs finds the right documentation and reports how strong the match is.
#
# The first call loads the embedding model and the keyword index (several seconds), and the Hugging Face
# warning is harmless.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 3b_test_search_docs.py
import re

import tools
from failure_source import FixtureFailureSource

# search_docs does not use the failure source, but build_tools needs one
source = FixtureFailureSource("incidents")
by_name = {t.name: t for t in tools.build_tools(source)}
search_docs = by_name["search_docs"]

# The doc path of every numbered passage in a result, e.g. "core-concepts/tasks.rst"
def sources_of(text):
    return [m.group(1) for m in re.finditer(r"^\[\d+\] (?:airflow|spark) \| ([^|]+) \|", text, re.M)]

print("TEST 1: an out-of-memory failure finds the Airflow troubleshooting page (inc-01)")
r1 = search_docs.invoke({"query": "task killed with return code -9 SIGKILL out of memory"})
print("  header:", r1.splitlines()[1][:90])
print("  strong match:", "STRONG match" in r1.splitlines()[1])
print("  troubleshooting.rst found:", any(s.strip().endswith("troubleshooting.rst") for s in sources_of(r1)))

print("TEST 2: a sensor timeout finds the Timeouts section (inc-03)")
r2 = search_docs.invoke({"query": "AirflowSensorTimeout sensor timeout reschedule mode"})
print("  sources:", sources_of(r2)[:4])
print("  tasks.rst or sensors.rst found:", any(("tasks.rst" in s or "sensors.rst" in s) for s in sources_of(r2)))

print("TEST 3: the system filter keeps to one product (inc-05)")
r3 = search_docs.invoke({"query": "container killed by YARN memory overhead", "system": "spark"})
print("  all sources are Spark:", " airflow |" not in r3)
print("  configuration.md found:", any("configuration.md" in s for s in sources_of(r3)))

# A similarity score measures topic closeness, not whether the passages answer the failure. These
# failures are not explained by the docs (the first is out of scope, the other three are in the company's
# code or schema), so none of them should come back as a STRONG match.
print("TEST 4: out-of-scope and enterprise failures must not be reported as strong matches")
queries = {
    "inc-11 snowflake": "Snowflake resource monitor quota exceeded warehouse cannot be resumed",
    "inc-08 column type": "invalid input syntax for type integer customer_zip cast",
    "inc-09 renamed column": "psycopg2 UndefinedColumn column does not exist",
    "inc-10 missing grant": "InsufficientPrivilege permission denied for table",
}
for label, q in queries.items():
    line = search_docs.invoke({"query": q}).splitlines()[1]
    print(f"  {label:<24} {line[:62]}   strong = {'STRONG match' in line}")

print("TEST 5: a wrong system value is refused")
try:
    search_docs.invoke({"query": "retries", "system": "kafka"})
    print("  FAIL: no error")
except ValueError as e:
    print("  PASS:", e)

print("\nSample (first 700 characters of the TEST 2 result):\n")
print(r2[:700])
