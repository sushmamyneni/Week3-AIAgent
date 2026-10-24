# Step 7e test: the whole trigger path with the real agent. A failure appears, the watcher queues it, the worker
# runs the agent, a human is notified, the ticket waits at the gate, and only an approval writes it.
#
# It makes about 10 model calls per incident (four incidents) and loads the embedding model once, so expect
# 3 to 5 minutes. Everything is written in temporary folders, never into the project.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 7e_test_trigger_end_to_end.py
import json
import shutil
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

import agent
import outbox
import tickets
import tools
import watcher
import worker
from failure_source import FixtureFailureSource
from incident_queue import IncidentQueue

work = Path(tempfile.mkdtemp())
incoming, box, ticket_dir = work / "incoming", work / "outbox", work / "tickets"
agent.cfg["paths"]["tickets_dir"] = str(ticket_dir)             # the approval step writes here, not into the project
source = FixtureFailureSource(incoming)                         # the agent reads its logs from the failure source
queue = IncidentQueue(work / "incidents.db")
FIXTURES = agent.cfg["paths"]["fixtures_dir"]
cap = agent.cfg["watcher"]["max_concurrent_runs"]

def drain(app):
    results = []
    while (r := worker.process_one(queue, app, box, cap)) is not None:
        results.append(r)
    return results

with SqliteSaver.from_conn_string(str(work / "checkpoints.db")) as saver:
    tools_by_name = {t.name: t for t in tools.build_tools(source)}
    app = agent.build_graph(source, tools_by_name, agent.make_llm(1500), checkpointer=saver)

    print("TEST 1: failures appear; the watcher queues each once, even when one is reported twice")
    for incident in ("inc-08", "inc-11", "inc-08"):
        watcher.simulate_failure(FIXTURES, incoming, incident)
    print("  queued two, not three:", len(watcher.poll_once(source, queue)) == 2 and len(queue.rows()) == 2)
    print("  nothing new on the next look:", watcher.poll_once(source, queue) == [])

    print("TEST 2: the worker runs each failure once, and a human is told what is needed")
    results = drain(app)
    by_id = {r["incident_id"]: r for r in queue.rows()}
    print("  two runs:", len(results) == 2 and all(r["attempts"] == 1 for r in queue.rows()))
    print("  inc-08 waits for approval:", by_id["inc-08"]["status"] == "waiting_approval")
    print("  inc-11 is escalated and done:", by_id["inc-11"]["status"] == "done" and by_id["inc-11"]["outcome"] == "escalated")
    kinds = sorted(n["kind"] for n in outbox.list_notifications(box))
    print("  notifications: one approval request, one escalation:", kinds == ["approval_needed", "escalation"])
    note = next(n for n in outbox.list_notifications(box) if n["kind"] == "approval_needed")
    print("  the approval request names the change:", any("sql/orders_load.sql:6" in line for line in note["lines"]))

    print("TEST 3: nothing is written until a human approves")
    print("  no ticket file:", not ticket_dir.exists() or list(ticket_dir.glob("*")) == [])

    print("TEST 4: a human answers the waiting ticket")
    key = by_id["inc-08"]["dedupe_key"]
    config = worker.thread_config({"thread_id": by_id["inc-08"]["thread_id"], "incident_id": "inc-08"})
    shown = app.get_state(config).values["draft_ticket"]
    wrong = worker.resolve(queue, app, key, {"decision": "approve", "reviewer": "sushma", "ticket_hash": "0000000000000000"})
    print("  a stale approval is refused and the run keeps waiting:", wrong["resolved"] is False and "ticket_hash" in wrong["error"]
          and queue.get(key)["status"] == "waiting_approval")
    done = worker.resolve(queue, app, key, {"decision": "approve", "reviewer": "sushma", "ticket_hash": tickets.ticket_hash(shown)})
    print("  the right approval resolves it:", done == {"resolved": True, "outcome": "ticket_created"} and queue.get(key)["status"] == "done")
    written = list(ticket_dir.glob("*.json"))
    print("  exactly one ticket, approved:", len(written) == 1 and json.loads(written[0].read_text())["status"] == "approved")
    print("  answering again changes nothing:", worker.resolve(queue, app, key, {"decision": "approve", "reviewer": "x"})["resolved"] is False
          and len(list(ticket_dir.glob("*.json"))) == 1)

    print("TEST 5: a failure that is reported again starts no new run")
    watcher.simulate_failure(FIXTURES, incoming, "inc-08")
    print("  not queued again:", watcher.poll_once(source, queue) == [] and drain(app) == [] and len(queue.rows()) == 2)

    print("TEST 6: a burst of failures is worked through, one run each")
    for incident in ("inc-04", "inc-09"):
        watcher.simulate_failure(FIXTURES, incoming, incident)
    print("  both queued:", len(watcher.poll_once(source, queue)) == 2)
    burst = drain(app)
    rows = {r["incident_id"]: r for r in queue.rows()}
    print("  both worked, one attempt each:", len(burst) == 2 and all(rows[i]["attempts"] == 1 for i in ("inc-04", "inc-09")))
    print("  statuses:", {i: rows[i]["status"] for i in ("inc-04", "inc-09")})
    print("  still only the one approved ticket:", len(list(ticket_dir.glob("*.json"))) == 1)

shutil.rmtree(work, ignore_errors=True)