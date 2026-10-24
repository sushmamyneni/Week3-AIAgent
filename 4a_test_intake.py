# Step 4a test: the intake node turns an event into one failed run and reads its cleaned log, and hands
# the failure to a human when there is no run or no log.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 4a_test_intake.py
import json
import shutil
import tempfile
from pathlib import Path

import agent
import tools
from failure_source import FixtureFailureSource

source = FixtureFailureSource("incidents")
tools_by_name = {t.name: t for t in tools.build_tools(source)}
intake = agent.make_intake(source, tools_by_name)

print("TEST 1: a full event is accepted and its log is read cleaned (inc-08)")
full = source.list_failures(name="orders_load")[0]
out = intake({"event": full})
print("  status running:", out["status"] == "running")
print("  source_ref kept:", out["incident"]["source_ref"] == "inc-08-column-type-changed")
print("  dedupe key built:", out["incident"]["dedupe_key"] == "airflow|orders_load|scheduled__2026-10-22T02:00:00+00:00")
print("  log in delimiters:", out["log_text"].count("<untrusted_log>") == 1)
print("  secret gone:", "Pa55w0rd-NOT-REAL" not in out["log_text"])
print("  counters start clean:", out["tool_calls_used"] == 0 and out["evidence"] == [] and out["notes"] == [])

print("TEST 2: a thin event (only a system and a name) is resolved to the latest failure")
out = intake({"event": {"system": "airflow", "dag_id": "orders_load"}})
print("  resolved:", out["status"] == "running" and out["incident"]["incident_id"] == "inc-08")

print("TEST 3: a thin event with a run id picks that run")
out = intake({"event": {"system": "airflow", "dag_id": "orders_load",
                        "run_id": "scheduled__2026-10-22T02:00:00+00:00"}})
print("  resolved:", out["status"] == "running" and out["incident"]["incident_id"] == "inc-08")

print("TEST 4: an event that matches no failed run is escalated")
for event in ({"system": "airflow", "dag_id": "orders_load", "run_id": "no-such-run"},
              {"system": "spark", "app_name": "ghost_job"}):
    out = intake({"event": event})
    print("  escalated:", out["status"] == "escalated", "|", out["escalation_reason"])

print("TEST 5: a failure whose log is missing is escalated, not a crash")
folder = Path(tempfile.mkdtemp())
(folder / "inc-x").mkdir()
(folder / "inc-x" / "event.json").write_text(json.dumps({
    "incident_id": "inc-x", "system": "airflow", "dag_id": "d", "run_id": "r1", "task_id": "t",
    "state": "failed", "failed_at": "2026-10-22T00:00:00Z"}))
bare = FixtureFailureSource(folder)
bare_tools = {t.name: t for t in tools.build_tools(bare)}
out = agent.make_intake(bare, bare_tools)({"event": {"system": "airflow", "dag_id": "d"}})
print("  escalated:", out["status"] == "escalated", "|", out["escalation_reason"])
shutil.rmtree(folder, ignore_errors=True)
