# Step 7a: the watcher. Looks at the failure source every few seconds and puts each NEW failure on the queue.
# A failure it has seen before is ignored, so one failure starts one run however often it is seen.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 7a_run_watcher.py            keep watching (Ctrl+C to stop)
#   python 7a_run_watcher.py --once     look once, then exit
import sys
import time
from pathlib import Path

import agent
from failure_source import FixtureFailureSource
from incident_queue import IncidentQueue
from watcher import poll_once

paths, settings = agent.cfg["paths"], agent.cfg["watcher"]
Path(paths["failure_source_dir"]).mkdir(exist_ok=True)
source = FixtureFailureSource(paths["failure_source_dir"])
queue = IncidentQueue(Path(paths["data_dir"]) / "incidents.db")

while True:
    new = poll_once(source, queue)
    for event in new:
        print(f"queued {event.get('incident_id', '?')}: {event['system']} {event.get('dag_id') or event.get('app_name')}", flush=True)
    if not new:
        print("no new failures", flush=True)
    if "--once" in sys.argv:
        break
    time.sleep(settings["poll_seconds"])