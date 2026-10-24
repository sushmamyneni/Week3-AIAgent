# Step 3a test: get_failure_log and its helpers (redaction, trimming, hidden instructions), using the
# real fixtures.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 3a_test_get_failure_log.py
import tools
from failure_source import FixtureFailureSource

# The fixtures folder holds the 12 incidents (and their answer keys, which the source never opens)
source = FixtureFailureSource("incidents")
by_name = {t.name: t for t in tools.build_tools(source)}
get_failure_log = by_name["get_failure_log"]

# The fake secrets planted in the fixtures. None may survive redaction.
SECRETS = {
    "inc-01-airflow-oom": "Wh-Pa55w0rd-NOT-REAL",
    "inc-02-airflow-no-retries": "fake_live_key_NOT_REAL_0000",
    "inc-08-column-type-changed": "Pa55w0rd-NOT-REAL",
}

print("TEST 1: secrets are redacted, useful text is kept")
for incident, secret in SECRETS.items():
    out = get_failure_log.invoke({"source_ref": incident})
    print(f"  {incident}: secret gone = {secret not in out}, redactions = {out.split('redacted: ')[1].split(',')[0]}")
out8 = get_failure_log.invoke({"source_ref": "inc-08-column-type-changed"})
print("  user and host kept:", "etl_user" in out8 and "warehouse.acme.internal" in out8)
print("  error text kept:", "InvalidTextRepresentation" in out8)

print("TEST 2: a long log is trimmed to the useful parts")
lines = [f"[2026-10-21T04:00:{i % 60:02d}] INFO - heartbeat {i}" for i in range(5000)]
lines[2500] = "[2026-10-21T05:00:00] ERROR - Task failed with exception"
lines[2501] = "ZeroDivisionError: division by zero"
long_log = "\n".join(lines)
trimmed, was_trimmed = tools.trim_log(long_log, 6000, 10, 30, 5)
print(f"  original {len(long_log):,} chars -> {len(trimmed):,} chars, trimmed = {was_trimmed}")
print("  error kept:", "ZeroDivisionError" in trimmed, "| gaps marked:", "lines omitted" in trimmed)
print("  a short log is left alone:", tools.trim_log("one line", 6000, 10, 30, 5) == ("one line", False))

print("TEST 3: hidden instructions are flagged")
out3 = get_failure_log.invoke({"source_ref": "inc-03-airflow-sensor-timeout"})
print("  inc-03 flagged:", "SECURITY NOTE" in out3)
print("  inc-01 not flagged:", "SECURITY NOTE" not in get_failure_log.invoke({"source_ref": "inc-01-airflow-oom"}))
print("  the log is wrapped in delimiters:", out3.count("<untrusted_log>") == 1 and out3.count("</untrusted_log>") == 1)

print("TEST 4: a missing log raises an error")
try:
    get_failure_log.invoke({"source_ref": "inc-99-does-not-exist"})
    print("  FAIL: no error")
except FileNotFoundError as e:
    print("  PASS:", e)

print("\nSample output (inc-08):\n")
print(out8[:900])
