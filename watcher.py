# The watcher: finds new failures and puts each one on the queue once. Also the failure simulator.
#
# Today the failure source is a folder (config: paths.failure_source_dir, "incoming"). A real deployment would
# read the Airflow REST API or the Spark history server through the same two methods of failure_source.py,
# and nothing here would change.
import re
import shutil
from pathlib import Path

# One look at the failure source. Every failure it lists is offered to the queue; the queue's duplicate check
# decides which are new. Returns only the new ones.
def poll_once(source, queue) -> list:
    return [event for event in source.list_failures() if queue.enqueue(event)]

# The simulator: stands in for "a job failed". It copies one fixture incident (its event.json and task.log,
# NEVER the answer key expected.json) into the failure source, as a real failure would appear there. Asking
# for the same incident twice leaves one copy: the same failure is the same failure.
# Returns "added" or "already there".
def simulate_failure(fixtures_dir, incoming_dir, incident_id: str) -> str:
    if not re.fullmatch(r"inc-\d{2}", incident_id):
        raise ValueError(f"{incident_id!r} is not an incident id such as inc-08")
    matches = sorted(Path(fixtures_dir).glob(f"{incident_id}-*"))
    if not matches:
        raise ValueError(f"no fixture for {incident_id}")
    source = matches[0]
    target = Path(incoming_dir) / source.name
    if target.exists():
        return "already there"
    target.mkdir(parents=True)
    for name in ("event.json", "task.log"):             # the answer key stays behind
        shutil.copy2(source / name, target / name)
    return "added"