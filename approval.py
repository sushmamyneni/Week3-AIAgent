# The approval gate and the write (step 6b).
#
# After the diagnosis drafts a ticket, the run PAUSES here. interrupt() saves the whole state to the checkpoint
# database and stops; nothing happens until a human answers (the inbox in step 8, or the command-line runner).
# The answer comes back from outside, so it is treated as untrusted input and checked in plain code:
#
#   approve   write the ticket as drafted
#   edit      change only new_value, bounds or root_cause, then write it
#   reject    write nothing
#
# The model never reaches the write. create_ticket (tickets.py) is called only from act(), which only runs
# after an approve or edit that passed every check below.
import copy
from datetime import datetime, timezone

from langgraph.types import interrupt
from pydantic import ValidationError

import agent
import tickets
from agent import AgentState

# The only fields a reviewer may change. The target, the evidence, the owner and the signals stay as the
# checks of step 5 left them: a reviewer cannot use an edit to get around those checks.
EDITABLE = ("new_value", "bounds", "root_cause")

# How many unusable answers are tolerated before the run gives up (and writes nothing)
MAX_ATTEMPTS = 3

# Check an answer from outside against the ticket that was shown. Returns (problem, ticket to write).
# A problem is a sentence for the reviewer ("" if there is none); the ticket is None when there is a problem
# or when the answer is a rejection.
def check_answer(answer, ticket: dict, shown_hash: str):
    if not isinstance(answer, dict) or answer.get("decision") not in ("approve", "edit", "reject"):
        return 'The answer must be a dictionary with decision "approve", "edit" or "reject".', None
    decision = answer["decision"]
    if decision == "reject":
        return "", None
    # An approval is a signature on a particular ticket: it must name who signs and echo the fingerprint
    reviewer = (answer.get("reviewer") or "").strip()
    if not tickets.APPROVER.fullmatch(reviewer):
        return "reviewer must be a short name or an email address.", None
    if answer.get("ticket_hash") != shown_hash:
        return "The ticket_hash does not match the ticket that was shown: this approval is for a different version.", None

    final = copy.deepcopy(ticket)
    edits = answer.get("edits") or {}
    if decision == "approve" and edits:
        return 'An "approve" cannot carry edits. Use "edit".', None
    if decision == "edit" and not edits:
        return 'An "edit" needs at least one edit.', None
    for field, value in edits.items():
        if field not in EDITABLE:
            return f"{field!r} cannot be edited. Editable: {', '.join(EDITABLE)}.", None
        if not isinstance(value, str):
            return f"The edit of {field!r} must be text.", None
        if field == "root_cause":
            final["root_cause"] = value.strip()
        else:
            final["proposed_action"][field] = value.strip()
    action = final["proposed_action"]
    if action["old_value"] and action["new_value"] == action["old_value"]:
        return "The new value is the same as the old value.", None
    try:                                   # the edited ticket must still fit the contract
        tickets.Ticket.model_validate(final)
    except ValidationError as e:
        return f"The edited ticket does not fit the contract: {e.errors()[0]['msg']}", None
    return "", final

# The gate. Everything BEFORE interrupt() runs again when the run resumes, so it only reads and never writes.
def approval(state: AgentState) -> dict:
    ticket = state["draft_ticket"]
    shown_hash = tickets.ticket_hash(ticket)
    error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # The value given to interrupt() is what the reviewer is shown. What it returns is the answer.
        answer = interrupt({"type": "approve_ticket", "ticket_id": ticket["ticket_id"], "ticket_hash": shown_hash,
                            "ticket": ticket, "editable_fields": list(EDITABLE),
                            "options": ["approve", "edit", "reject"], "attempt": attempt, "error": error})
        error, final = check_answer(answer, ticket, shown_hash)
        if not error:
            break
    else:                                  # three unusable answers in a row: stop, and write nothing
        return {"approval": {"decision": "none", "reason": f"{MAX_ATTEMPTS} unusable answers: {error}"},
                "outcome": "approval_failed", "status": "done"}

    record = {"decision": answer["decision"], "reviewer": (answer.get("reviewer") or "").strip() or "unknown",
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ticket_hash": shown_hash,
              "edited": answer["decision"] == "edit", "reason": str(answer.get("reason") or "")[:300]}
    if answer["decision"] == "reject":
        return {"approval": record, "outcome": "rejected", "status": "done"}
    return {"approval": record, "final_ticket": final}

# The write. Runs only after an approval that passed the checks. create_ticket writes the file once.
def act(state: AgentState) -> dict:
    result = tickets.create_ticket(state["final_ticket"], state["approval"]["reviewer"], agent.cfg["paths"]["tickets_dir"])
    return {"outcome": "ticket_created" if result["created"] else "already_created",
            "ticket_path": result["path"], "status": "done"}

# After the gate: write, or end (rejected, or too many unusable answers)
def route_after_approval(state: AgentState) -> str:
    return "end" if state.get("outcome") else "act"