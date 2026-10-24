# The outbox: simulated notifications. Each one is a small JSON file in the outbox folder, standing in for a
# message to Slack or an email. The inbox page shows them. Sending real ones would replace this one function.
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

# Write one notification. There is at most one per (kind, key): the file name is built from both, and the file
# is created in exclusive mode, so a repeated event or a repeated call cannot send the same message twice.
# Returns the path, or None if this notification was already sent.
def notify(folder, kind: str, key: str, incident_id: str, title: str, lines: list):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{kind}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}.json"
    body = {"kind": kind, "key": key, "incident_id": incident_id, "title": title, "lines": lines,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(json.dumps(body, indent=2))
    except FileExistsError:
        return None
    print(f"[outbox] {title}")
    return path

# All notifications, newest first
def list_notifications(folder) -> list:
    items = [json.loads(p.read_text(encoding="utf-8")) for p in Path(folder).glob("*.json")] if Path(folder).exists() else []
    return sorted(items, key=lambda n: n["created_at"], reverse=True)