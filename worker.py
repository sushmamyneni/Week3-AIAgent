# The worker: takes failures from the queue, runs the agent on each, and notifies a human when one is needed.
#
# The worker never writes a ticket and never decides anything: it starts the graph. The graph pauses at the
# approval gate by itself, and only a human's answer (resolve, below, called from the inbox) lets it go on.
import outbox
from langgraph.types import Command

# Lines for a notification about a ticket that waits for approval
def describe_ticket(ticket: dict) -> list:
    action = ticket["proposed_action"]
    change = (f"{action['type']} in {action['target']['file']}:{action['target']['line']}: "
              f"{action['old_value']!r} -> {action['new_value']!r}") if action.get("target") else action["type"]
    return [f"Root cause: {ticket['root_cause']}", f"Proposed: {change}", f"Owner: {ticket['owner'] or 'none'}",
            f"Ticket {ticket['ticket_id']} is a DRAFT: nothing is written until you approve it in the inbox."]

# Lines for a notification about a failure the agent could not diagnose
def describe_escalation(escalation: dict) -> list:
    lines = [f"Why: {escalation['reason']}"]
    lines += [f"Found: {f}" for f in escalation["found"][:5]]
    lines += [f"Not found: {n}" for n in escalation["not_found"][:3]]
    lines.append(f"Route to: {escalation['owner'] or 'whoever is on call'}. Nothing was written.")
    return lines

def thread_config(job: dict) -> dict:
    return {"configurable": {"thread_id": job["thread_id"]}, "recursion_limit": 60,
            "run_name": f"triage:{job['incident_id']}", "metadata": {"incident_id": job["incident_id"]}}

# Run the oldest queued failure. Returns what happened, or None if there was nothing to run.
def process_one(queue, app, outbox_dir, max_running: int):
    job = queue.claim_next(max_running)
    if job is None:
        return None
    key, incident_id, config = job["dedupe_key"], job["incident_id"], thread_config(job)
    try:
        app.invoke({"event": job["event"]}, config)
        state = app.get_state(config)
    except Exception as e:                  # a crashed run writes nothing: it is marked failed and can be queued again
        error = f"{type(e).__name__}: {str(e)[:200]}"
        queue.mark(key, "failed", error=error)
        outbox.notify(outbox_dir, "run_failed", f"{key}#{job['attempts']}", incident_id,
                      f"The agent crashed on {incident_id}",
                      [f"Error: {error}", "Nothing was written.", "It can be queued again (7c_run_worker.py --requeue-failed)."])
        return {"key": key, "status": "failed", "error": error}

    values = state.values
    if state.next:                          # paused at the approval gate: a ticket is waiting for a human
        queue.mark(key, "waiting_approval", outcome="ticket_drafted")
        outbox.notify(outbox_dir, "approval_needed", key, incident_id, f"Approval needed: {incident_id}",
                      describe_ticket(values["draft_ticket"]))
        return {"key": key, "status": "waiting_approval"}
    if values.get("decision") == "escalate":
        queue.mark(key, "done", outcome="escalated")
        outbox.notify(outbox_dir, "escalation", key, incident_id, f"Needs a human: {incident_id}",
                      describe_escalation(values["escalation"]))
        return {"key": key, "status": "done", "outcome": "escalated"}
    queue.mark(key, "done", outcome=values.get("outcome", ""))
    return {"key": key, "status": "done", "outcome": values.get("outcome", "")}

# A human's answer to a waiting run (the inbox calls this). The answer is checked by the approval gate itself;
# here the run is resumed and the queue row is updated. If the answer was not acceptable, the gate asks again
# and the run stays waiting: the reason is returned so the inbox can show it.
def resolve(queue, app, key: str, answer: dict) -> dict:
    row = queue.get(key)
    if row is None or row["status"] != "waiting_approval":
        return {"resolved": False, "error": "this failure is not waiting for approval"}
    config = thread_config({"thread_id": row["thread_id"], "incident_id": row["incident_id"]})
    result = app.invoke(Command(resume=answer), config)
    if app.get_state(config).next:
        pending = result["__interrupt__"][0].value
        return {"resolved": False, "error": pending.get("error") or "the answer was not accepted"}
    outcome = app.get_state(config).values.get("outcome", "")
    queue.mark(key, "done", outcome=outcome)
    return {"resolved": True, "outcome": outcome}