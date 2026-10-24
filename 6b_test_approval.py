# Step 6b test: the approval gate. The run pauses before every write; approve writes exactly one ticket, reject
# writes nothing, edit changes only what a reviewer may change, and bad answers change nothing.
#
# Part A tests the gate on its own, with no model calls (it includes a resume from a SEPARATE process, which
# is what the inbox will do). Part B runs the real agent on two incidents (about 20 model calls).
# Every ticket is written inside a temporary folder, never into the project.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 6b_test_approval.py
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

import agent
import approval
import tickets
from agent import AgentState

# ---------------------------------------------------------------------------
# Shared setup
# ---------------------------------------------------------------------------

# A draft ticket as the diagnosis step leaves it in the state. Each failure gets its own ticket id.
def draft(failure="airflow|orders_load|run-1", **changes):
    data = {
        "ticket_id": tickets.ticket_id_for(failure), "incident_id": "inc-08", "system": "airflow",
        "run": {"dag_or_app": "orders_load", "run_id": "run-1", "task": "load_orders", "failed_at": "2026-10-22T02:21:30Z"},
        "root_cause": "orders.customer_zip became VARCHAR(10) in migration 0044, but the load still casts it to INTEGER.",
        "evidence": [{"kind": "code", "ref": "E1", "quote": "s.customer_zip::INTEGER", "citation": "sql/orders_load.sql:6"}],
        "proposed_action": {"type": "code_change", "target": {"file": "sql/orders_load.sql", "line": 6, "key": "customer_zip"},
                            "old_value": "s.customer_zip::INTEGER", "new_value": "s.customer_zip"},
        "confidence_signals": {"best_doc_score": None, "sources_agreeing": 3, "change_allowlisted": False},
        "owner": "@acme/orders-team",
    }
    data.update(changes)
    return tickets.Ticket(**data).model_dump()

# The gate on its own: approval -> act. The same nodes and the same route as in the real graph.
def gate_graph(saver):
    graph = StateGraph(AgentState)
    graph.add_node("approval", approval.approval)
    graph.add_node("act", approval.act)
    graph.add_edge(START, "approval")
    graph.add_conditional_edges("approval", approval.route_after_approval, {"act": "act", "end": END})
    graph.add_edge("act", END)
    return graph.compile(checkpointer=saver)

thread = lambda name: {"configurable": {"thread_id": name}}
start = lambda t: {"draft_ticket": t, "decision": "ticket"}

# ---------------------------------------------------------------------------
# Child processes: the same file, run again with a mode, to prove a pause survives a restart
# ---------------------------------------------------------------------------
if len(sys.argv) > 1 and sys.argv[1] in ("child_start", "child_resume"):
    mode, db, folder = sys.argv[1], sys.argv[2], sys.argv[3]
    agent.cfg["paths"]["tickets_dir"] = folder
    ticket = draft("airflow|orders_load|run-process")
    with SqliteSaver.from_conn_string(db) as saver:
        app = gate_graph(saver)
        if mode == "child_start":
            out = app.invoke(start(ticket), thread("cross-process"))
            print("PAUSED" if app.get_state(thread("cross-process")).next == ("approval",) else "NOT PAUSED")
        else:
            saved_ticket = app.get_state(thread("cross-process")).values["draft_ticket"]     # what process 1 saved
            app.invoke(Command(resume={"decision": "approve", "reviewer": "second.process",
                                       "ticket_hash": tickets.ticket_hash(saved_ticket)}), thread("cross-process"))
            print("RESUMED", app.get_state(thread("cross-process")).values.get("outcome"))
    sys.exit(0)

work = Path(tempfile.mkdtemp())
folder = work / "tickets"
agent.cfg["paths"]["tickets_dir"] = str(folder)          # every write of this test goes to the temporary folder
written = lambda: sorted(p.name for p in folder.glob("*.json")) if folder.exists() else []

# ---------------------------------------------------------------------------
# Part A: the gate on its own
# ---------------------------------------------------------------------------
with SqliteSaver.from_conn_string(str(work / "gate.db")) as saver:
    app = gate_graph(saver)

    print("TEST 1: the run pauses at the gate, shows the ticket and its fingerprint, and writes nothing")
    a = draft("airflow|orders_load|run-A")
    shown = app.invoke(start(a), thread("A"))["__interrupt__"][0].value
    print("  paused at the approval step:", app.get_state(thread("A")).next == ("approval",))
    print("  the reviewer is shown the ticket and its hash:", shown["ticket"] == a and shown["ticket_hash"] == tickets.ticket_hash(a))
    print("  options and editable fields offered:", shown["options"] == ["approve", "edit", "reject"]
          and shown["editable_fields"] == ["new_value", "bounds", "root_cause"])
    print("  nothing written:", written() == [])

    print("TEST 2: approve writes exactly one ticket, with the approver recorded")
    app.invoke(Command(resume={"decision": "approve", "reviewer": "sushma", "ticket_hash": shown["ticket_hash"]}), thread("A"))
    state = app.get_state(thread("A"))
    file_a = folder / f"{a['ticket_id']}.json"
    print("  outcome ticket_created, run finished:", state.values["outcome"] == "ticket_created" and state.next == ())
    print("  one file:", written() == [file_a.name])
    saved = json.loads(file_a.read_text(encoding="utf-8"))
    print("  status approved by sushma:", saved["status"] == "approved" and saved["approved_by"] == "sushma")

    print("TEST 3: reject writes nothing")
    b = draft("airflow|orders_load|run-B")
    app.invoke(start(b), thread("B"))
    app.invoke(Command(resume={"decision": "reject", "reviewer": "sushma", "reason": "not the right fix"}), thread("B"))
    state = app.get_state(thread("B"))
    print("  outcome rejected, run finished:", state.values["outcome"] == "rejected" and state.next == ())
    print("  no new file:", written() == [file_a.name])
    print("  the rejection is on record:", state.values["approval"]["reviewer"] == "sushma" and state.values["approval"]["reason"] == "not the right fix")

    print("TEST 4: an edit changes only what a reviewer may change")
    c = draft("airflow|orders_load|run-C")
    shown = app.invoke(start(c), thread("C"))["__interrupt__"][0].value
    app.invoke(Command(resume={"decision": "edit", "reviewer": "sushma", "ticket_hash": shown["ticket_hash"],
                               "edits": {"new_value": "CAST(s.customer_zip AS VARCHAR(10))", "bounds": "text only"}}), thread("C"))
    saved = json.loads((folder / f"{c['ticket_id']}.json").read_text(encoding="utf-8"))
    print("  edited value written:", saved["proposed_action"]["new_value"] == "CAST(s.customer_zip AS VARCHAR(10))"
          and saved["proposed_action"]["bounds"] == "text only")
    print("  target, owner and evidence untouched:", saved["proposed_action"]["target"] == c["proposed_action"]["target"]
          and saved["owner"] == c["owner"] and saved["evidence"] == c["evidence"])

    print("TEST 5: answers that are not acceptable change nothing, and the gate asks again")
    d = draft("airflow|orders_load|run-D")
    good_hash = tickets.ticket_hash(d)
    bad_answers = {
        "a stale fingerprint": {"decision": "approve", "reviewer": "sushma", "ticket_hash": "0000000000000000"},
        "no fingerprint": {"decision": "approve", "reviewer": "sushma"},
        "a reviewer that looks like a path": {"decision": "approve", "reviewer": "../../x", "ticket_hash": good_hash},
        "no reviewer": {"decision": "approve", "ticket_hash": good_hash},
        "an unknown decision": {"decision": "merge", "reviewer": "sushma", "ticket_hash": good_hash},
        "not a dictionary": "approve",
        "an approve that carries edits": {"decision": "approve", "reviewer": "sushma", "ticket_hash": good_hash, "edits": {"new_value": "x"}},
        "an edit of the owner": {"decision": "edit", "reviewer": "sushma", "ticket_hash": good_hash, "edits": {"owner": "@evil/team"}},
        "an edit of the target file": {"decision": "edit", "reviewer": "sushma", "ticket_hash": good_hash, "edits": {"file": "../../.env"}},
        "an edit with no edits": {"decision": "edit", "reviewer": "sushma", "ticket_hash": good_hash},
        "an edit that changes nothing": {"decision": "edit", "reviewer": "sushma", "ticket_hash": good_hash,
                                         "edits": {"new_value": "s.customer_zip::INTEGER"}},
    }
    for n, (name, answer) in enumerate(bad_answers.items()):
        name_thread = thread(f"D{n}")                       # one run per bad answer
        shown = app.invoke(start(d), name_thread)["__interrupt__"][0].value
        out = app.invoke(Command(resume=answer), name_thread)
        again = out["__interrupt__"][0].value if "__interrupt__" in out else {}
        print(f"  {name}: asked again with a reason = {app.get_state(name_thread).next == ('approval',) and bool(again.get('error'))}")
    print("  nothing was written by any of them:", written() == sorted([file_a.name, f"{c['ticket_id']}.json"]))

    print("TEST 6: three unusable answers in a row end the run, and nothing is written")
    e = draft("airflow|orders_load|run-E")
    app.invoke(start(e), thread("E"))
    for _ in range(3):
        app.invoke(Command(resume="approve"), thread("E"))
    state = app.get_state(thread("E"))
    print("  run ended without a write:", state.values["outcome"] == "approval_failed" and state.next == ())
    print("  no file for it:", not (folder / f"{e['ticket_id']}.json").exists())

    print("TEST 7: the same failure approved twice still gives one ticket")
    before = written()
    app.invoke(start(a), thread("A-again"))                     # the same failure arrives again, as a new run
    app.invoke(Command(resume={"decision": "approve", "reviewer": "someone.else", "ticket_hash": tickets.ticket_hash(a)}), thread("A-again"))
    print("  second run reports already_created:", app.get_state(thread("A-again")).values["outcome"] == "already_created")
    print("  still the same files, first one untouched:", written() == before
          and json.loads(file_a.read_text(encoding="utf-8"))["approved_by"] == "sushma")
    try:                                                         # answering a run that is already finished
        app.invoke(Command(resume={"decision": "approve", "reviewer": "late.click", "ticket_hash": tickets.ticket_hash(a)}), thread("A"))
    except Exception:
        pass
    print("  a late second click on the finished run writes nothing:", written() == before)

# ---------------------------------------------------------------------------
# Part A, continued: a pause survives a restart
# ---------------------------------------------------------------------------
print("TEST 8: a run paused in one process is resumed by another process")
db = str(work / "process.db")
run = lambda mode: subprocess.run([sys.executable, __file__, mode, db, str(folder)], capture_output=True, text=True, timeout=120)
files_before = written()
first = run("child_start")
print("  process 1 paused at the gate:", "PAUSED" in first.stdout)
print("  nothing written while it waited:", written() == files_before)
second = run("child_resume")
print("  process 2 resumed and approved:", "RESUMED ticket_created" in second.stdout)
print("  exactly one new ticket:", len(written()) == len(files_before) + 1)

# ---------------------------------------------------------------------------
# Part B: the real agent
# ---------------------------------------------------------------------------
import tools
from failure_source import FixtureFailureSource

source = FixtureFailureSource(agent.cfg["paths"]["fixtures_dir"])
tools_by_name = {t.name: t for t in tools.build_tools(source)}

def run_real(incident_id, saver):
    app = agent.build_graph(source, tools_by_name, agent.make_llm(1500), checkpointer=saver)
    event = next(ev for ev in source.list_failures() if ev["incident_id"] == incident_id)
    cfg = thread(f"real-{incident_id}")
    out = app.invoke({"event": event}, {**cfg, "recursion_limit": 60})
    return app, cfg, out

with SqliteSaver.from_conn_string(str(work / "real.db")) as saver:
    print("TEST 9: the real agent drafts a ticket for inc-08, pauses, and writes only after approval")
    before = written()
    app, cfg, out = run_real("inc-08", saver)
    paused = app.get_state(cfg).next == ("approval",)
    print("  paused at the gate:", paused)
    print("  nothing written yet:", written() == before)
    if paused:
        shown = out["__interrupt__"][0].value
        target = shown["ticket"]["proposed_action"]["target"]
        print("  the ticket shown targets sql/orders_load.sql:6:", (target["file"], target["line"]) == ("sql/orders_load.sql", 6))
        app.invoke(Command(resume={"decision": "approve", "reviewer": "sushma", "ticket_hash": shown["ticket_hash"]}), cfg)
        final = app.get_state(cfg).values
        print("  outcome ticket_created:", final["outcome"] == "ticket_created")
        print("  one new file, approved:", len(written()) == len(before) + 1
              and tickets.load_ticket(shown["ticket_id"], folder).status == "approved")

    print("TEST 10: an out-of-scope failure (inc-11) is escalated: no pause, no write")
    before = written()
    app, cfg, out = run_real("inc-11", saver)
    final = app.get_state(cfg)
    print("  decision escalate:", final.values["decision"] == "escalate")
    print("  did not pause, run finished:", final.next == ())
    print("  nothing written:", written() == before and "outcome" not in final.values)

shutil.rmtree(work, ignore_errors=True)