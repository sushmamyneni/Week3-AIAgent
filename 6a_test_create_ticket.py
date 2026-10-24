# Step 6a test: create_ticket writes an approved ticket exactly once, refuses anything invalid, and never
# overwrites. No model calls. It writes only inside a temporary folder.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 6a_test_create_ticket.py
import json
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

import tickets
from tickets import Ticket

folder = Path(tempfile.mkdtemp()) / "tickets"          # a fresh folder: nothing here touches the project

# A draft ticket as the diagnosis step produces it (a dictionary, status draft)
def draft(**changes):
    data = {
        "ticket_id": tickets.ticket_id_for("airflow|orders_load|scheduled__2026-10-22T02:00:00+00:00"),
        "incident_id": "inc-08", "system": "airflow",
        "run": {"dag_or_app": "orders_load", "run_id": "scheduled__2026-10-22T02:00:00+00:00",
                "task": "load_orders", "failed_at": "2026-10-22T02:21:30Z"},
        "root_cause": "orders.customer_zip became VARCHAR(10) in migration 0044, but the load still casts it to INTEGER.",
        "evidence": [{"kind": "code", "ref": "E1", "quote": "s.customer_zip::INTEGER", "citation": "sql/orders_load.sql:6"}],
        "proposed_action": {"type": "code_change",
                            "target": {"file": "sql/orders_load.sql", "line": 6, "key": "customer_zip"},
                            "old_value": "s.customer_zip::INTEGER", "new_value": "s.customer_zip"},
        "confidence_signals": {"best_doc_score": None, "sources_agreeing": 3, "change_allowlisted": False},
        "owner": "@acme/orders-team",
    }
    data.update(changes)
    return Ticket(**data).model_dump()                  # the same shape the agent keeps in its state

files = lambda: sorted(p.name for p in folder.glob("*")) if folder.exists() else []

print("TEST 1: an approved ticket is written, with the status and the approver filled in")
t = draft()
out = tickets.create_ticket(t, "sushma.myneni@match.com", folder)
written = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
print("  created:", out["created"] is True and files() == [f"{t['ticket_id']}.json"])
print("  status approved, approver recorded:", written["status"] == "approved" and written["approved_by"] == "sushma.myneni@match.com")
print("  mandatory constraints kept:", written["constraints"] == tickets.MANDATORY_CONSTRAINTS)
print("  the caller's draft is unchanged:", t["status"] == "draft" and t["approved_by"] is None)

print("TEST 2: a second approval writes nothing (exactly once)")
before = Path(out["path"]).read_text(encoding="utf-8")
again = tickets.create_ticket(t, "someone.else", folder)
print("  reported as not created:", again["created"] is False)
print("  still one file, content untouched:", files() == [f"{t['ticket_id']}.json"] and Path(out["path"]).read_text(encoding="utf-8") == before)

print("TEST 3: an edited ticket for the same failure does not overwrite the first")
edited = draft(root_cause="A different root cause text that someone wrote later, long enough to be valid.")
print("  not created, first one intact:", tickets.create_ticket(edited, "sushma", folder)["created"] is False
      and json.loads(Path(out["path"]).read_text(encoding="utf-8"))["root_cause"] == t["root_cause"])

print("TEST 4: an invalid ticket is refused and leaves no file")
other = Path(tempfile.mkdtemp()) / "tickets"
bad = dict(draft(), proposed_action={"type": "code_change", "target": {"file": "../../.env", "line": 1}, "new_value": "x"})
try:
    tickets.create_ticket(bad, "sushma", other)
    print("  FAIL: accepted")
except ValidationError:
    print("  refused:", not other.exists() or list(other.glob("*")) == [])

print("TEST 5: the approver must be a short name or an email address")
for who in ("", "   ", None, "../../etc/x", "a" * 81, "name\nwith newline"):
    try:
        tickets.create_ticket(draft(ticket_id=tickets.ticket_id_for("another failure")), who, other)
        print("  FAIL: accepted", repr(who))
    except ValueError:
        print("  refused:", repr(who)[:30])
print("  nothing was written:", not other.exists() or list(other.glob("*")) == [])

print("TEST 6: a failure while writing leaves no half-written file, and a retry works")
failing_id = tickets.ticket_id_for("a third failure")
real = Ticket.model_dump_json
Ticket.model_dump_json = lambda self, **kw: (_ for _ in ()).throw(OSError("disk full"))
try:
    tickets.create_ticket(draft(ticket_id=failing_id), "sushma", other)
except OSError:
    pass
Ticket.model_dump_json = real
print("  no file left behind:", not (other / f"{failing_id}.json").exists())
print("  a later attempt succeeds:", tickets.create_ticket(draft(ticket_id=failing_id), "sushma", other)["created"] is True)

print("TEST 7: the fingerprint")
a = draft()
print("  same ticket, same hash:", tickets.ticket_hash(a) == tickets.ticket_hash(dict(reversed(list(a.items())))))
changed = dict(a, root_cause=a["root_cause"] + " (edited)")
print("  one field changed, different hash:", tickets.ticket_hash(a) != tickets.ticket_hash(changed))

print("TEST 8: reading a ticket back")
loaded = tickets.load_ticket(t["ticket_id"], folder)
print("  round trip:", loaded is not None and loaded.status == "approved" and loaded.incident_id == "inc-08")
print("  a missing ticket is None:", tickets.load_ticket("T-00000000", folder) is None)
print("  a path-like id is refused, not read:", tickets.load_ticket("../../config", folder) is None)

shutil.rmtree(folder.parent, ignore_errors=True)
shutil.rmtree(other.parent, ignore_errors=True)