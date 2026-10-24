# The inbox logic: what the approval page shows and does. The page (8a_inbox_app.py) only draws it.
#
# Everything a person can do here goes through worker.resolve, which resumes the paused run with the person's
# answer. The approval gate then checks that answer itself (the fingerprint, the reviewer, the editable fields),
# so the page adds no way around the gate: it cannot write a ticket on its own.
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

import agent
import outbox
import tickets
import tools
import worker
from approval import EDITABLE
from failure_source import FixtureFailureSource
from incident_queue import IncidentQueue

@dataclass
class Runtime:
    queue: IncidentQueue
    app: object

# Open the queue and the agent graph on the shared database files. The saved runs are in SQLite, so a run the
# worker paused in another process can be resumed here. check_same_thread=False because the page runs in several
# threads; timeout makes a busy database a short wait instead of an error.
def open_runtime() -> Runtime:
    paths = agent.cfg["paths"]
    data = Path(paths["data_dir"])
    data.mkdir(exist_ok=True)
    conn = sqlite3.connect(data / "checkpoints.db", check_same_thread=False, timeout=30)
    source = FixtureFailureSource(paths["failure_source_dir"])
    tools_by_name = {t.name: t for t in tools.build_tools(source)}
    app = agent.build_graph(source, tools_by_name, agent.make_llm(1500), checkpointer=SqliteSaver(conn))
    return Runtime(IncidentQueue(data / "incidents.db"), app)

# The runs waiting for a decision, each with the ticket and its fingerprint as they are NOW
def waiting(rt: Runtime) -> list:
    items = []
    for row in rt.queue.rows("waiting_approval"):
        ticket = rt.app.get_state(worker.thread_config(row)).values.get("draft_ticket")
        if ticket:
            items.append({"key": row["dedupe_key"], "incident_id": row["incident_id"], "ticket": ticket,
                          "hash": tickets.ticket_hash(ticket)})
    return items

# Only what the person really changed is sent as an edit (an unchanged field is not an edit)
def changed_edits(ticket: dict, new_value: str, bounds: str, root_cause: str) -> dict:
    now = {"new_value": ticket["proposed_action"]["new_value"], "bounds": ticket["proposed_action"]["bounds"],
           "root_cause": ticket["root_cause"]}
    entered = {"new_value": new_value.strip(), "bounds": bounds.strip(), "root_cause": root_cause.strip()}
    return {field: entered[field] for field in EDITABLE if entered[field] != now[field]}

# Send a person's decision to the paused run. shown_hash is the fingerprint of the ticket the person SAW; the
# gate refuses the answer if the ticket is no longer that version.
def answer(rt: Runtime, key: str, decision: str, reviewer: str, shown_hash: str, edits: dict = None, reason: str = "") -> dict:
    reply = {"decision": decision, "reviewer": reviewer, "ticket_hash": shown_hash}
    if edits:
        reply["edits"] = edits
    if reason:
        reply["reason"] = reason
    return worker.resolve(rt.queue, rt.app, key, reply)

def notifications() -> list:
    return outbox.list_notifications(agent.cfg["paths"]["outbox_dir"])

# One line per failure for the overview table
def failures(rt: Runtime) -> list:
    return [{"incident": r["incident_id"], "system": r["system"], "name": r["name"], "status": r["status"],
             "outcome": r["outcome"], "attempts": r["attempts"], "error": r["error"], "created": r["created_at"]}
            for r in rt.queue.rows()]