# Step 8a: the approval inbox. A page for the person who gets the notifications.
#
# Run it with:   streamlit run 8a_inbox_app.py
#
# Three tabs: tickets waiting for a decision (approve, edit, or reject), the notifications the agent sent
# (including escalations, which need a person but have nothing to approve), and every failure the agent has
# seen. All the logic is in inbox.py; the safety checks are in the approval gate (approval.py).
#
# Text that came from logs, code or the model is shown with st.text, st.code and tables, never as markdown, so
# something like "[click here](http://...)" written into a log cannot become a link on this page.
import streamlit as st

import inbox

st.set_page_config(page_title="Failure triage inbox", layout="wide")

# One connection to the queue and the saved runs for the whole session
@st.cache_resource
def runtime():
    return inbox.open_runtime()

rt = runtime()
st.title("Failure triage inbox")
st.caption("Nothing is written until you approve. Every ticket below is a draft made by the agent.")

with st.sidebar:
    reviewer = st.text_input("Your name (recorded as the approver)", key="reviewer")
    if st.button("Refresh", key="refresh"):
        st.rerun()

# A message from the last action stays on screen after the page redraws
if "flash" in st.session_state:
    kind, text = st.session_state.pop("flash")
    (st.success if kind == "ok" else st.error)(text)

waiting = inbox.waiting(rt)
tab_waiting, tab_notes, tab_all = st.tabs([f"Waiting for approval ({len(waiting)})", "Notifications", "All failures"])

# --- Tickets waiting for a decision
with tab_waiting:
    if not waiting:
        st.info("Nothing is waiting for approval.")
    for item in waiting:
        key, ticket = item["key"], item["ticket"]
        action = ticket["proposed_action"]
        seen_key = f"seen_{key}"
        seen_hash = st.session_state.get(seen_key, item["hash"])    # the version of the ticket this person last saw
        st.session_state[seen_key] = item["hash"]
        target = action.get("target")
        label = (f"{item['incident_id']}: {action['type']} in {target['file']}:{target['line']}" if target
                 else f"{item['incident_id']}: {action['type']}")
        with st.expander(label, expanded=True):
            if seen_hash != item["hash"]:
                st.warning("This ticket changed since you last looked at it. Review it again before deciding.")
            st.text(f"Run: {ticket['system']} {ticket['run']['dag_or_app']}  task {ticket['run']['task']}  failed at {ticket['run']['failed_at']}")
            st.text(f"Root cause: {ticket['root_cause']}")
            if target:
                st.text(f"Change in {target['file']} line {target['line']}  ({target['key']})")
                st.code(f"- {action['old_value']}\n+ {action['new_value']}", language="diff")
            if action["bounds"]:
                st.text(f"Allowed range: {action['bounds']}")
            st.text(f"Owner: {ticket['owner']}    Ticket: {ticket['ticket_id']}    Fingerprint: {item['hash']}")
            signals = ticket["confidence_signals"]
            st.text(f"Signals: {signals['sources_agreeing']} kinds of evidence agree | docs score {signals['best_doc_score']} | "
                    f"pre-approved kind of change: {signals['change_allowlisted']} | validated: {signals['validation']}")
            st.text("Evidence (each quote was checked against the source before it was cited):")
            st.dataframe([{"kind": e["kind"], "where": e["citation"], "quote": e["quote"]} for e in ticket["evidence"]],
                         hide_index=True, width="stretch")

            with st.form(f"form_{key}"):
                st.text("You may edit these three fields. Nothing else in the ticket can be changed.")
                new_value = st.text_input("New value", action["new_value"], key=f"new_{key}")
                bounds = st.text_input("Allowed range", action["bounds"], key=f"bounds_{key}")
                root_cause = st.text_area("Root cause", ticket["root_cause"], key=f"cause_{key}")
                reason = st.text_input("Reason (kept with a rejection)", key=f"reason_{key}")
                c1, c2, c3 = st.columns(3)
                approve = c1.form_submit_button("Approve as drafted", key=f"approve_{key}")
                edit = c2.form_submit_button("Save my edit and approve", key=f"edit_{key}")
                reject = c3.form_submit_button("Reject", key=f"reject_{key}")

            if approve or edit or reject:
                edits = inbox.changed_edits(ticket, new_value, bounds, root_cause)
                if not reviewer.strip():
                    st.error("Type your name in the sidebar first: it is recorded as the approver.")
                elif edit and not edits:
                    st.error("You did not change anything. Use 'Approve as drafted', or change a field first.")
                elif approve and edits:
                    st.error("You changed a field. Use 'Save my edit and approve', or put the field back.")
                else:
                    decision = "reject" if reject else ("edit" if edit else "approve")
                    result = inbox.answer(rt, key, decision, reviewer.strip(), seen_hash, edits=None if reject else edits, reason=reason)
                    if result["resolved"]:
                        st.session_state["flash"] = ("ok", f"{item['incident_id']}: {decision} done. Outcome: {result['outcome']}")
                        st.rerun()
                    else:
                        st.error(f"Not accepted: {result['error']}")

# --- What the agent has told people
with tab_notes:
    notes = inbox.notifications()
    if not notes:
        st.info("No notifications yet.")
    for note in notes:
        with st.container(border=True):
            st.text(f"[{note['kind']}]  {note['title']}    {note['created_at']}")
            for line in note["lines"]:
                st.text(line)

# --- Every failure the agent has seen
with tab_all:
    rows = inbox.failures(rt)
    st.dataframe(rows, hide_index=True, width="stretch") if rows else st.info("No failures yet.")