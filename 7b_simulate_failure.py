# Step 7b: the failure simulator. Stands in for "an Airflow DAG run or a Spark job failed": it drops a fixture
# incident (its event and its log, never the answer key) into the failure source. If the watcher is running,
# it notices by itself.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 7b_simulate_failure.py inc-08            one failure
#   python 7b_simulate_failure.py inc-08 inc-11     several at once (a burst)
#   python 7b_simulate_failure.py --all             all twelve
import sys

import agent
from watcher import simulate_failure

paths = agent.cfg["paths"]
ids = [f"inc-{n:02d}" for n in range(1, 13)] if "--all" in sys.argv else [a for a in sys.argv[1:] if not a.startswith("--")]
if not ids:
    sys.exit(__doc__)
for incident_id in ids:
    print(f"{incident_id}: {simulate_failure(paths['fixtures_dir'], paths['failure_source_dir'], incident_id)}")