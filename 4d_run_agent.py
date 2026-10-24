# Step 4d: run the agent on one failure, from the command line.
#
# It runs the graph built in agent.py (intake -> triage -> evidence loop) on one fixture incident, saves the
# run in the SQLite checkpoint database, and prints a readable report. This is also the debugging runner the
# plan calls for: the watcher in step 7 will start the same graph automatically.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 4d_run_agent.py inc-08                 run the agent on one failure
#   python 4d_run_agent.py inc-08 --show          print the saved result of an earlier run (no model calls)
#   python 4d_run_agent.py inc-08 --rerun         investigate again, as a new run (the old one is kept)
#   python 4d_run_agent.py inc-08 --trace         also send a trace to LangSmith (uploads the prompts and
#                                                 answers of the run to the LangSmith cloud; here that is
#                                                 only fake fixture data)
import os
import sys
import time
from pathlib import Path

# Tracing is OFF unless asked for. These variables must be set before anything is imported that makes a
# traced call, so this comes first.
if "--trace" in sys.argv:
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGSMITH_PROJECT", "week3-agent")

from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

import agent
import tools
import tickets
from failure_source import FixtureFailureSource, dedupe_key

# Flags with a value (--approve NAME, --reject NAME) and plain flags (--show, --rerun, --trace)
args, flags, values = [], set(), {}
tokens = iter(sys.argv[1:])
for token in tokens:
    if token in ("--approve", "--reject"):
        values[token] = next(tokens, None)
    elif token.startswith("--"):
        flags.add(token)
    else:
        args.append(token)
if len(args) != 1:
    sys.exit(__doc__)

# The fixtures play the part of the failure source, as in every test so far
source = FixtureFailureSource(agent.cfg["paths"]["fixtures_dir"])
event = next((e for e in source.list_failures() if e["incident_id"] == args[0]), None)
if event is None:
    sys.exit(f"No incident named {args[0]!r}. Try inc-01 to inc-12.")

# The saved runs live in SQLite, in data/ (ignored by git)
db = Path(agent.cfg["paths"]["data_dir"]) / "checkpoints.db"
db.parent.mkdir(exist_ok=True)

# A thread_id names one run. Using the failure's own identity means one failure is one run: asking for the
# same failure again finds the saved run instead of starting a second one.
thread_id = dedupe_key(event)
if "--rerun" in flags:
    thread_id += f"#rerun-{int(time.time())}"

# Print what a run found. Step 5 will add the diagnosis and the proposed action to this report.
def print_report(state: dict):
    incident, triage = state["incident"], state.get("triage", {})
    print(f"\nIncident {incident['incident_id']}: {incident['system']} {agent.name_of(incident)} "
          f"(task {incident.get('task_id')}), failed at {incident['failed_at']}")
    print(f"Status: {state['status']}", f"| reason: {state['escalation_reason']}" if state.get("escalation_reason") else "")
    if triage:
        print(f"Triage: {triage['failure_kind']} | {triage['error_summary']}")
        print(f"  identifiers: {triage['identifiers']}")
    print(f"Evidence ({state['tool_calls_used']} of {agent.agent_cfg['max_tool_calls']} tool calls):")
    for n, e in enumerate(state["evidence"], start=1):
        first_line = e["result"].strip().splitlines()[0][:70] if e["result"].strip() else ""
        print(f"  {n}. {e['tool']}({', '.join(f'{k}={v!r}' for k, v in e['args'].items())}) "
              f"-> {'ok' if e['ok'] else 'FAILED'} | {first_line}")
    for note in state["notes"]:
        print(f"  note: {note}")
    if state.get("evidence_summary"):
        print(f"Closing note from the evidence step:\n  {state['evidence_summary'].strip()}")

    # The outcome: a draft ticket (or retry request), or a hand-off report. Nothing is written yet: step 6 puts
    # a human's approval in front of every write.
    print(f"\nDecision: {state.get('decision', 'none')}")
    ticket, esc = state.get("draft_ticket"), state.get("escalation")
    if ticket:
        a, s = ticket["proposed_action"], ticket["confidence_signals"]
        print(f"Draft ticket {ticket['ticket_id']} ({ticket['status']}), owner {ticket['owner']}")
        print(f"  root cause: {ticket['root_cause']}")
        if a["target"]:
            print(f"  change ({a['type']}): {a['target']['file']}:{a['target']['line']}  {a['old_value']!r} -> {a['new_value']!r}")
        else:
            print(f"  action: {a['type']}")
        for e in ticket["evidence"]:
            print(f"  evidence [{e['kind']}] {e['citation']}: {e['quote'][:80]}")
        print(f"  signals: {s['sources_agreeing']} kinds of evidence agree, docs score {s['best_doc_score']}, "
              f"allowlisted {s['change_allowlisted']}, validation {s['validation']}")
    if esc:
        print(f"Escalation to {esc['owner'] or 'whoever is on call'}: {esc['reason']}")
        for line in esc["found"]:
            print(f"  found: {line}")
        for line in esc["not_found"]:
            print(f"  not found: {line}")
    for d in state.get("dropped", []):
        print(f"  dropped citation: {d}")
    if state.get("approval"):
        a = state["approval"]
        print(f"Approval: {a['decision']} by {a.get('reviewer', '-')}" + (f" ({a['reason']})" if a.get("reason") else ""))
    if state.get("outcome"):
        print(f"Outcome: {state['outcome']}" + (f" -> {state['ticket_path']}" if state.get("ticket_path") else ""))

# The checkpointer saves the state after every step. The "with" block closes the database cleanly.
with SqliteSaver.from_conn_string(str(db)) as saver:
    tools_by_name = {t.name: t for t in tools.build_tools(source)}
    app = agent.build_graph(source, tools_by_name, agent.make_llm(1500), checkpointer=saver)
    # run_name and metadata label the trace in LangSmith. recursion_limit allows enough graph steps for
    # the loop (every tool call is two steps).
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50,
              "run_name": f"triage:{event['incident_id']}", "metadata": {"incident_id": event["incident_id"]}}

    saved = app.get_state(config)
    waiting = bool(saved.values) and bool(saved.next)            # paused at the approval gate
    already_run = bool(saved.values) and not saved.next          # a finished run is on file for this failure

    if "--show" in flags:
        if not saved.values:
            sys.exit(f"No saved run for {event['incident_id']}. Run it first, without --show.")
        print_report(saved.values)
        if waiting:
            print(f"\nWAITING FOR APPROVAL. Resume with --approve NAME or --reject NAME.")
    elif waiting and values:
        # Answer the pause. The fingerprint comes from the saved ticket, as the inbox will echo it from the page.
        decision = "approve" if "--approve" in values else "reject"
        reviewer = values.get("--approve") or values.get("--reject")
        answer = {"decision": decision, "reviewer": reviewer, "ticket_hash": tickets.ticket_hash(saved.values["draft_ticket"])}
        state = app.invoke(Command(resume=answer), config)
        print_report(app.get_state(config).values)
    elif waiting:
        print(f"{event['incident_id']} is waiting for approval.")
        print_report(saved.values)
        print("\nWAITING FOR APPROVAL. Resume with --approve NAME or --reject NAME.")
    elif already_run:
        print(f"{event['incident_id']} was already investigated (same failure, same run). Showing the saved result.")
        print("Use --rerun to investigate again as a new run.")
        print_report(saved.values)
    else:
        started = time.time()
        state = app.invoke({"event": event}, config)
        print(f"Run finished in {time.time() - started:.0f}s.")
        after = app.get_state(config)
        print_report(after.values)
        # Prove the run was saved: read it back from the database
        print(f"\nSaved in {db}: {bool(after.values)} | " +
              ("PAUSED at the approval gate: nothing has been written. Resume with --approve NAME or --reject NAME."
               if after.next else "nothing left to run"))

if "--trace" in flags:
    wait_for_all_tracers()                  # tracing uploads in the background: wait, or the script may exit first
    print(f"Trace sent to LangSmith project '{os.environ['LANGSMITH_PROJECT']}'.")