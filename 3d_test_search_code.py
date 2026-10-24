# Step 3d test: the search_code tool finds the failing line, the file and the owner, flags hidden
# instructions in code comments, and keeps its path argument safe.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 3d_test_search_code.py
import re

import tools
from failure_source import FixtureFailureSource

# search_code does not use the failure source, but build_tools needs one
source = FixtureFailureSource("incidents")
by_name = {t.name: t for t in tools.build_tools(source)}
search_code = by_name["search_code"]

print("TEST 1: the code search finds the failing line, the file and the owner (inc-08)")
r1 = search_code.invoke({"query": "customer_zip INTEGER cast"})
print("  file, lines and owner:", "sql/orders_load.sql:1-10 | owner: @acme/orders-team" in r1)
print("  the cast line is marked:", ">>   6  " in r1 and "s.customer_zip::INTEGER" in r1)
print("  wrapped in delimiters:", r1.count("<untrusted_code>") == 1 and r1.count("</untrusted_code>") == 1)

print("TEST 2: a hidden instruction in a code comment is flagged")
r2 = search_code.invoke({"query": "orders_load"})
print("  flagged:", "SECURITY NOTE" in r2, "| mentions AI assistant:", "AI assistant" in r2)

print("TEST 3: path_prefix keeps the search inside one folder, and bad prefixes are refused")
r3 = search_code.invoke({"query": "retries", "path_prefix": "dags/"})
paths = re.findall(r"^(\S+?):\d+-\d+ \| owner", r3, re.M)
print("  every hit is under dags/:", bool(paths) and all(p.startswith("dags/") for p in paths))
for bad in ("../..", "/etc", "C:\\Windows"):
    try:
        search_code.invoke({"query": "x", "path_prefix": bad})
        print("  FAIL: accepted", repr(bad))
    except ValueError:
        print("  refused:", repr(bad))

print("TEST 4: a query with no match says so")
print("  ", search_code.invoke({"query": "zzzqqq_nothing_like_this"}).splitlines()[1][:60])

print("\nTools available:", sorted(by_name))
