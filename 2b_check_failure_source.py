# Step 2b check: the failure source finds all 12 failures and can read their logs.
#
# For this test it reads the fixtures folder, which also holds the answer keys (expected.json). The
# failure source never opens expected.json, and the agent will only ever be pointed at incoming\.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 2b_check_failure_source.py
import yaml

from failure_source import FixtureFailureSource, dedupe_key, name_of

# The folder comes from config.yaml (paths.fixtures_dir), so no path is hardcoded here
cfg = yaml.safe_load(open("config.yaml"))
source = FixtureFailureSource(cfg["paths"]["fixtures_dir"])

# All failures, oldest first
events = source.list_failures()
print(f"{len(events)} failures found\n")
print(f"{'incident':<9} {'system':<8} {'name':<26} {'run or app id':<40} log lines")
for e in events:
    log = source.read_log(e["source_ref"])
    # A thin event has neither a run id nor an application id
    run = e.get("run_id") or e.get("app_id") or "(thin: none)"
    print(f"{e['incident_id']:<9} {e['system']:<8} {name_of(e):<26} {run:<40} {len(log.splitlines())}")

# A few checks that must hold for the rest of the project to work
keys = [dedupe_key(e) for e in events]
print("\n12 events:", len(events) == 12)
print("every dedupe key is unique:", len(set(keys)) == len(keys))
print("thin events (no run id and no app id):",
      [e["incident_id"] for e in events if not (e.get("run_id") or e.get("app_id"))])
print("filter by name works:", [e["incident_id"] for e in source.list_failures(name="inventory_refresh")])
print("filter by system works:", len(source.list_failures(system="spark")), "spark failures")

# The path guard: a path that escapes the folder must be refused
try:
    source.read_log("../../.env")
    print("path guard: FAIL, it read a file outside the folder")
except (ValueError, FileNotFoundError) as e:
    print("path guard: PASS,", type(e).__name__)
