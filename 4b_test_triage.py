# Step 4b test: the triage node classifies each failure from its log. It uses the real model (one small
# call per incident, 12 in all), so run it when the Nebius key is set.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 4b_test_triage.py
import agent
import tools
from failure_source import FixtureFailureSource

source = FixtureFailureSource("incidents")
tools_by_name = {t.name: t for t in tools.build_tools(source)}
intake = agent.make_intake(source, tools_by_name)
triage = agent.make_triage(agent.make_llm())

# Run intake and triage on every incident. The answer key (expected.json) is never read: the checks
# below are written by hand and only look for obvious things.
results = {}
for event in source.list_failures():
    state = intake({"event": event})
    state["event"] = event
    results[event["incident_id"]] = triage(state)["triage"]

print("TEST 1: every incident gets a complete, valid triage")
for incident_id, t in results.items():
    ok = (t["system"] in ("airflow", "spark", "other") and t["error_summary"] and t["identifiers"]
          and t["suggested_sources"] and len(t["identifiers"]) <= 12)
    print(f"  {incident_id}: {t['system']:<8} {t['failure_kind']:<24} -> {ok}")

print("TEST 2: the right system, and the identifiers that matter are copied from the log")
checks = {
    "inc-01": ("airflow", "inventory_refresh"),
    "inc-05": ("spark", "spark.executor.memoryOverhead"),
    "inc-08": ("airflow", "customer_zip"),
    "inc-09": ("airflow", "cust_email"),
    "inc-10": ("airflow", "customer_segments"),
}
for incident_id, (system, identifier) in checks.items():
    t = results[incident_id]
    found = any(identifier in i for i in t["identifiers"])
    print(f"  {incident_id}: system {t['system'] == system}, identifier {identifier!r} found: {found}")

print("TEST 3: a failure about another system is flagged (inc-11 Snowflake)")
print("  other_system names Snowflake:", "snowflake" in results["inc-11"]["other_system"].lower())

print("TEST 4: a model that keeps failing gives an 'unknown' triage and a note, not a crash")
class BrokenModel:
    calls = 0
    def with_structured_output(self, schema):
        return self
    def invoke(self, messages):
        BrokenModel.calls += 1
        raise ConnectionError("network down")
broken = agent.make_triage(BrokenModel())
state = intake({"event": source.list_failures(name="orders_load")[0]})
state["event"] = source.list_failures(name="orders_load")[0]
out = broken(state)
print("  tried twice:", BrokenModel.calls == 1 + agent.agent_cfg["tool_retries"])
print("  fallback is unknown:", out["triage"]["failure_kind"] == "unknown")
print("  note added:", len(out["notes"]) == 1 and "Triage failed twice" in out["notes"][0])

print("\nSample (inc-08):")
for key, value in results["inc-08"].items():
    print(f"  {key}: {value}")
