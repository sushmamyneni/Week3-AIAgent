# Step 7d test: the incident queue, the watcher, the simulator, the outbox and the worker's bookkeeping. No model
# calls: the worker is tested with a stand-in for the agent graph. Everything is written in a temporary folder.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 7d_test_queue_watcher.py
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import outbox
import watcher
import worker
from failure_source import FixtureFailureSource
from incident_queue import IncidentQueue

work = Path(tempfile.mkdtemp())
incoming, box = work / "incoming", work / "outbox"
queue = IncidentQueue(work / "incidents.db")
source = FixtureFailureSource(incoming)
FIXTURES = "incidents"

print("TEST 1: the simulator copies an incident into the failure source, without the answer key")
print("  added:", watcher.simulate_failure(FIXTURES, incoming, "inc-08") == "added")
folder = next(incoming.glob("inc-08-*"))
print("  event and log copied, answer key not:", sorted(p.name for p in folder.iterdir()) == ["event.json", "task.log"])
print("  the same failure twice leaves one copy:", watcher.simulate_failure(FIXTURES, incoming, "inc-08") == "already there"
      and len(list(incoming.glob("inc-08-*"))) == 1)
for bad in ("inc-99", "../../x", "inc-8", "inc-08/../inc-01"):
    try:
        watcher.simulate_failure(FIXTURES, incoming, bad)
        print("  FAIL: accepted", repr(bad))
    except ValueError:
        print("  refused:", repr(bad))

print("TEST 2: the watcher queues a new failure once")
print("  first look queues it:", [e["incident_id"] for e in watcher.poll_once(source, queue)] == ["inc-08"])
print("  second look queues nothing:", watcher.poll_once(source, queue) == [])
print("  one row, status queued:", [r["status"] for r in queue.rows()] == ["queued"])

print("TEST 3: a burst of failures is queued once each, oldest first")
for n in (11, 1, 5, 9):
    watcher.simulate_failure(FIXTURES, incoming, f"inc-{n:02d}")
print("  four new:", len(watcher.poll_once(source, queue)) == 4)
print("  again, nothing new:", watcher.poll_once(source, queue) == [] and len(queue.rows()) == 5)
print("  the same failure offered 20 times is one row:", all(not queue.enqueue(source.list_failures()[0]) for _ in range(20)) and len(queue.rows()) == 5)

print("TEST 4: claiming a failure")
first = queue.claim_next(max_running=2)
second = queue.claim_next(max_running=2)
third = queue.claim_next(max_running=2)
print("  oldest first, marked running:", first is not None and queue.get(first["dedupe_key"])["status"] == "running")
print("  two at once allowed, a third refused (the limit):", second is not None and third is None)
print("  the first attempt's thread is the dedupe key:", first["thread_id"] == first["dedupe_key"] and first["attempts"] == 1)
print("  never the same failure twice:", first["dedupe_key"] != second["dedupe_key"])
queue.mark(first["dedupe_key"], "done", outcome="x")
print("  a finished run frees a slot:", queue.claim_next(max_running=2) is not None)

print("TEST 5: a crashed worker is detected, and the failure can be queued again")
sql = sqlite3.connect(work / "incidents.db")
stale = second["dedupe_key"]
sql.execute("UPDATE incidents SET started_at = '2020-01-01T00:00:00+00:00' WHERE dedupe_key = ?", (stale,))
sql.commit(); sql.close()
print("  the old running row is marked failed:", queue.recover_crashed(900) == [stale] and queue.get(stale)["status"] == "failed")
print("  a recent running row is left alone:", queue.recover_crashed(900) == [])
print("  requeue works only for failed rows:", queue.requeue(stale) is True and queue.requeue(stale) is False)
retry = queue.claim_next(max_running=5)
print("  the retry gets its own checkpoint thread:", retry["dedupe_key"] == stale and retry["thread_id"] == f"{stale}#attempt-2")

print("TEST 6: a notification is sent once")
p1 = outbox.notify(box, "approval_needed", "key-1", "inc-08", "Approval needed: inc-08", ["a line"])
p2 = outbox.notify(box, "approval_needed", "key-1", "inc-08", "Approval needed: inc-08", ["a line"])
print("  first written, repeat refused:", p1 is not None and p2 is None and len(list(box.glob("*.json"))) == 1)
print("  a different kind for the same failure is separate:", outbox.notify(box, "escalation", "key-1", "inc-08", "t", []) is not None)

# A stand-in for the agent graph: it reports what the real graph would leave behind
class FakeApp:
    def __init__(self, behaviour): self.behaviour, self.runs = behaviour, []
    def invoke(self, payload, config):
        self.runs.append(config["configurable"]["thread_id"])
        if self.behaviour == "crash":
            raise RuntimeError("model unreachable")
    def get_state(self, config):
        if self.behaviour == "ticket":
            ticket = {"ticket_id": "T-0123abcd", "root_cause": "cause text", "owner": "@acme/orders-team",
                      "proposed_action": {"type": "code_change", "target": {"file": "sql/orders_load.sql", "line": 6},
                                          "old_value": "a", "new_value": "b"}}
            return SimpleNamespace(next=("approval",), values={"draft_ticket": ticket, "decision": "ticket"})
        if self.behaviour == "escalate":
            esc = {"reason": "nothing explains it", "found": ["x"], "not_found": ["y"], "owner": None}
            return SimpleNamespace(next=(), values={"decision": "escalate", "escalation": esc})
        return SimpleNamespace(next=(), values={})

print("TEST 7: the worker turns the agent's result into a status and a notification")
def run_with(behaviour, label):
    q = IncidentQueue(work / f"{label}.db")
    q.enqueue(source.list_failures()[0])
    out = worker.process_one(q, FakeApp(behaviour), work / f"box-{label}", max_running=1)
    return q, out, sorted(p.name.split("-")[0] for p in (work / f"box-{label}").glob("*.json")) if (work / f"box-{label}").exists() else []
q, out, sent = run_with("ticket", "ticket")
print("  a ticket waits for approval and notifies:", out["status"] == "waiting_approval" and sent == ["approval_needed"]
      and q.rows()[0]["status"] == "waiting_approval")
note = json.loads(next((work / "box-ticket").glob("*.json")).read_text())
print("  the notification says it is only a draft:", any("DRAFT" in l for l in note["lines"]) and any("sql/orders_load.sql:6" in l for l in note["lines"]))
q, out, sent = run_with("escalate", "escalate")
print("  an escalation is done and notifies:", out["outcome"] == "escalated" and sent == ["escalation"] and q.rows()[0]["status"] == "done")
q, out, sent = run_with("crash", "crash")
print("  a crash marks the run failed, notifies, writes nothing else:", out["status"] == "failed" and sent == ["run_failed"]
      and q.rows()[0]["status"] == "failed" and "model unreachable" in q.rows()[0]["error"])
print("  an empty queue does nothing:", worker.process_one(IncidentQueue(work / "empty.db"), FakeApp("ticket"), work / "box-empty", 1) is None)

print("TEST 8: the queue survives a restart")
reopened = IncidentQueue(work / "incidents.db")
print("  same rows after reopening:", len(reopened.rows()) == 5 and not reopened.enqueue(source.list_failures()[0]))

shutil.rmtree(work, ignore_errors=True)