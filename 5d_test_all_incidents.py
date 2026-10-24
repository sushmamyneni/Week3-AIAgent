# Step 5d: run the whole agent on all 12 incidents and compare each result with its answer key.
#
# This is the first real accuracy number. For each incident the agent runs end to end (intake, triage, evidence
# loop, diagnosis) and the result is graded against incidents/<name>/expected.json: the expected action, the
# target file and line, and the owner. The agent never sees the answer key; only this script reads it.
# Nothing is written anywhere: a ticket is only a draft until the approval step in step 6.
#
# It makes about 10 model calls per incident and loads the embedding model once, so expect 5 to 10 minutes.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 5d_test_all_incidents.py                 all 12 incidents
#   python 5d_test_all_incidents.py inc-08 inc-10   only these
import json
import sys
import time
from pathlib import Path

import agent
import tools
from failure_source import FixtureFailureSource

source = FixtureFailureSource(agent.cfg["paths"]["fixtures_dir"])
tools_by_name = {t.name: t for t in tools.build_tools(source)}
app = agent.build_graph(source, tools_by_name, agent.make_llm(1500))
only = set(sys.argv[1:])

# A code change and a configuration change count as the same kind of action here: both are "a ticket with an
# exact change". Whether the model chose the exact label is reported separately.
def action_class(action):
    return "change" if action in ("code_change", "config_change") else action

rows = []
for event in source.list_failures():
    if only and event["incident_id"] not in only:
        continue
    expected = json.loads((Path(source.folder) / event["source_ref"] / "expected.json").read_text(encoding="utf-8"))
    started = time.time()
    try:
        state = app.invoke({"event": event}, {"recursion_limit": 60})
    except Exception as e:                    # a crash is a result too: it is reported, and the run continues
        rows.append({"id": event["incident_id"], "kind": expected["kind"], "crashed": f"{type(e).__name__}: {str(e)[:120]}"})
        print(f"{event['incident_id']}: CRASHED {rows[-1]['crashed']}", flush=True)
        continue

    ticket = state.get("draft_ticket")
    got = "escalate" if state["decision"] == "escalate" else ticket["proposed_action"]["type"]
    target = (ticket["proposed_action"].get("target") or {}) if ticket else {}
    want_target = expected.get("expected_target") or {}

    row = {"id": event["incident_id"], "kind": expected["kind"], "want": expected["expected_action"], "got": got,
           "calls": state["tool_calls_used"], "seconds": round(time.time() - started),
           "action_ok": action_class(got) == action_class(expected["expected_action"]),
           "label_ok": got == expected["expected_action"],
           "target_ok": None, "owner_ok": None, "keywords": None,
           "got_target": f"{target.get('file')}:{target.get('line')}" if target else "",
           "want_target": f"{want_target.get('file')}:{want_target.get('line')}" if want_target else "",
           "reason": state.get("escalation_reason", ""), "dropped": len(state.get("dropped", []))}
    if want_target:
        row["target_ok"] = bool(ticket) and (target.get("file"), target.get("line")) == (want_target["file"], want_target["line"])
    if expected.get("expected_owner"):
        row["owner_ok"] = bool(ticket) and ticket["owner"] == expected["expected_owner"]
    if ticket:                                # how many of the answer key's keywords the ticket mentions
        text = " ".join([ticket["root_cause"]] + [e["quote"] for e in ticket["evidence"]]
                        + [ticket["proposed_action"]["old_value"], ticket["proposed_action"]["new_value"]]).lower()
        words = expected["root_cause_keywords"]
        row["keywords"] = f"{sum(w.lower() in text for w in words)}/{len(words)}"
    row["correct"] = row["action_ok"] and row["target_ok"] is not False and row["owner_ok"] is not False
    # The answer key for a retry says: "if a retry tool is not built, the acceptable fallback is to escalate".
    # There is no retry tool yet, so an escalation counts as an accepted fallback (reported separately).
    row["fallback"] = expected["expected_action"] == "retry" and got == "escalate"
    rows.append(row)
    print(f"{row['id']}: wanted {row['want']}, got {row['got']} | correct = {row['correct']}{' (accepted fallback)' if row['fallback'] else ''} | {row['calls']} calls, {row['seconds']}s", flush=True)

# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
print("\nRESULTS")
print(f"{'incident':<8} {'kind':<13} {'wanted':<14} {'got':<14} {'target':<7} {'owner':<6} {'keywords':<9} {'calls':<5} correct")
for r in rows:
    if "crashed" in r:
        print(f"{r['id']:<8} {r['kind']:<13} CRASHED: {r['crashed']}")
        continue
    mark = lambda v: "-" if v is None else ("yes" if v else "NO")
    print(f"{r['id']:<8} {r['kind']:<13} {r['want']:<14} {r['got']:<14} {mark(r['target_ok']):<7} {mark(r['owner_ok']):<6} "
          f"{(r['keywords'] or '-'):<9} {r['calls']:<5} {'yes' if r['correct'] else ('fallback' if r['fallback'] else 'NO')}")

misses = [r for r in rows if "crashed" not in r and not r["correct"] and not r["fallback"]]
if misses:
    print("\nMISSES")
    for r in misses:
        print(f"  {r['id']}: wanted {r['want']} at {r['want_target'] or '-'}; got {r['got']} at {r['got_target'] or '-'}"
              + (f" | escalated because: {r['reason']}" if r["reason"] else ""))

graded = [r for r in rows if "crashed" not in r]
in_scope = [r for r in graded if r["want"] != "escalate"]
out_scope = [r for r in graded if r["want"] == "escalate"]
print("\nSUMMARY")
strict = sum(r["correct"] for r in in_scope)
fallback = sum(r["fallback"] for r in in_scope)
print(f"  in-scope incidents correct (action, target, owner): {strict} of {len(in_scope)} strictly, "
      f"{strict + fallback} counting the accepted retry fallback   (target: at least 8 of 10)")
print(f"  out-of-scope incidents escalated:                   {sum(r['correct'] for r in out_scope)} of {len(out_scope)}   (target: 2 of 2)")
print(f"  crashed runs: {len(rows) - len(graded)} | citations dropped in total: {sum(r['dropped'] for r in graded)} "
      f"| tool calls in total: {sum(r['calls'] for r in graded)}")
print(f"  nothing was written: the 'tickets' folder exists = {Path('tickets').exists()}")