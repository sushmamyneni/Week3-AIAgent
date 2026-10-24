# Step 7c: the worker. Takes queued failures one at a time and runs the agent on each. A run that needs a human
# (a ticket to approve, or an escalation) puts a notification in the outbox; a ticket run then WAITS at the
# approval gate until it is answered in the inbox (or with 4d_run_agent.py --approve).
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 7c_run_worker.py                    keep working (Ctrl+C to stop)
#   python 7c_run_worker.py --drain            run everything queued, then exit
#   python 7c_run_worker.py --requeue-failed   put failed runs back on the queue, then work as usual
import sys
import time
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

import agent
import tools
import worker
from failure_source import FixtureFailureSource
from incident_queue import IncidentQueue

paths, settings = agent.cfg["paths"], agent.cfg["watcher"]
queue = IncidentQueue(Path(paths["data_dir"]) / "incidents.db")
source = FixtureFailureSource(paths["failure_source_dir"])          # the same failure source the watcher reads

# A run still marked running from an earlier worker that died is marked failed, so it does not look alive
for key in queue.recover_crashed(settings["stale_run_seconds"]):
    print(f"marked failed (the worker stopped before it finished): {key}")
if "--requeue-failed" in sys.argv:
    for row in queue.rows("failed"):
        print(f"requeued {row['incident_id']}" if queue.requeue(row["dedupe_key"]) else "")

with SqliteSaver.from_conn_string(str(Path(paths["data_dir"]) / "checkpoints.db")) as saver:
    tools_by_name = {t.name: t for t in tools.build_tools(source)}
    app = agent.build_graph(source, tools_by_name, agent.make_llm(1500), checkpointer=saver)
    while True:
        result = worker.process_one(queue, app, paths["outbox_dir"], settings["max_concurrent_runs"])
        if result:
            print(f"{result['key']}: {result['status']}" + (f" ({result.get('outcome') or result.get('error')})" if result.get("outcome") or result.get("error") else ""), flush=True)
            continue                                                  # look for the next one straight away
        if "--drain" in sys.argv:
            break
        time.sleep(settings["poll_seconds"])
print("queue:", queue.counts())