# The failure source: how the rest of the system finds failed runs and reads their logs.
#
# Today it reads fixture folders (one folder per failure, with event.json and task.log). Later a real
# version could call the Airflow REST API and the Spark history server instead, with the same two
# methods, so the watcher, the tools and the agent would not change.
#
# The agent never sees expected.json (the answer key): this module only ever reads event.json and
# task.log.
import json
from pathlib import Path


# The name of the thing that failed: a DAG for Airflow, an application for Spark
def name_of(event: dict) -> str:
    return event.get("dag_id") or event.get("app_name")


# One failure must start exactly one agent run, so every event needs a stable identity.
# Prefer the run id (Airflow) or the application id (Spark). A thin event has neither, so fall back
# to the failure time, which is still unique for a given DAG.
def dedupe_key(event: dict) -> str:
    run = event.get("run_id") or event.get("app_id") or event.get("failed_at")
    return f"{event['system']}|{name_of(event)}|{run}"


class FixtureFailureSource:
    def __init__(self, folder):
        self.folder = Path(folder)

    # All failures in the folder, oldest first. Optional filters narrow by system ("airflow" or
    # "spark") and by DAG or application name; this is how a thin event (only a name) is resolved.
    # source_ref (the folder name) is added to each event: it is the handle used to read the log.
    def list_failures(self, system=None, name=None) -> list:
        events = []
        for event_file in sorted(self.folder.glob("*/event.json")):
            event = json.loads(event_file.read_text(encoding="utf-8"))
            event["source_ref"] = event_file.parent.name
            if system and event["system"] != system:
                continue
            if name and name_of(event) != name:
                continue
            events.append(event)
        return sorted(events, key=lambda e: e["failed_at"])

    # The raw log of one failure. This is the RAW text: trimming and redacting secrets happen in the
    # tool that the agent calls, not here.
    def read_log(self, source_ref: str) -> str:
        path = (self.folder / source_ref / "task.log").resolve()
        # source_ref can end up coming from the model, so treat it as untrusted: it must stay inside the
        # folder. A value like "../../.env" would otherwise read a file it should not.
        if not path.is_relative_to(self.folder.resolve()):
            raise ValueError("source_ref points outside the failure source")
        if not path.is_file():
            raise FileNotFoundError(f"no log for {source_ref}")
        return path.read_text(encoding="utf-8")
