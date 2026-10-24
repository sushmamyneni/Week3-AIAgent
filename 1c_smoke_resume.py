# Step 1c smoke test: pause a LangGraph run at an approval gate, save it to SQLite, and resume it
# from a SEPARATE process.
#
# This is the heart of the human-in-the-loop design: the agent drafts an action, pauses, and a human
# approves or rejects later. The toy graph below has three steps:
#     diagnose  ->  approval (pauses here)  ->  act (only if approved)
#
# It checks:
#   1. a run pauses at the approval step and its state is saved in SQLite
#   2. a NEW process can resume it (this is the part that matters)
#   3. "approve" performs the action exactly once, and "reject" performs nothing
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active). Run these TWO commands one after
# the other, as two separate processes:
#   python 1c_smoke_resume.py start
#   python 1c_smoke_resume.py resume
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

# Where the saved (checkpointed) state lives, and a log file that records every action performed.
# Both are in data/, which is ignored by git. The log lets us prove an action ran exactly once.
DB = Path("data/checkpoints.db")
ACTION_LOG = Path("data/smoke_actions.log")

# ---------------------------------------------------------------------------
# The toy graph
# ---------------------------------------------------------------------------

# The state is the dictionary that travels through the graph. Each node returns only the keys it
# wants to change, and LangGraph merges them in.
class State(TypedDict, total=False):
    incident: str       # which failure this is
    proposed: str       # the action the agent wants to take
    decision: str       # the human's answer: "approve" or "reject"

# Step 1: stands in for the agent's diagnosis. It just proposes an action.
def diagnose(state: State) -> dict:
    return {"proposed": f"create ticket for {state['incident']}"}

# Step 2: the approval gate. interrupt() stops the run RIGHT HERE and saves everything to the
# checkpointer. The value passed to interrupt() is what a human would be shown (in the real project:
# the inbox page). When the run is resumed later, interrupt() returns whatever the human answered.
#
# IMPORTANT: on resume, LangGraph runs this node again from its first line. Anything before the
# interrupt() call would run twice, so the code above it must be safe to repeat (here there is none).
def approval(state: State) -> dict:
    decision = interrupt({"question": "Approve this action?", "proposed": state["proposed"]})
    return {"decision": decision}

# Step 3: the write. It only runs if the human approved. It appends a line to the log, which is how
# we can count how many times it ran.
def act(state: State) -> dict:
    with ACTION_LOG.open("a") as f:
        f.write(f"{state['incident']}: {state['proposed']}\n")
    return {}

# After the approval step, go to "act" if approved, otherwise end without doing anything.
def route(state: State) -> str:
    return "act" if state["decision"] == "approve" else END

# Wire the steps together. The checkpointer is what saves the state at every step (and at the pause).
def build(saver):
    graph = StateGraph(State)
    graph.add_node("diagnose", diagnose)
    graph.add_node("approval", approval)
    graph.add_node("act", act)
    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "approval")
    graph.add_conditional_edges("approval", route, {"act": "act", END: END})
    graph.add_edge("act", END)
    return graph.compile(checkpointer=saver)

# A thread_id names one run. The same id later means "continue that run". One id per incident.
def config_for(name: str) -> dict:
    return {"configurable": {"thread_id": name}}

mode = sys.argv[1] if len(sys.argv) > 1 else ""

# ---------------------------------------------------------------------------
# Process 1: start two runs and let them pause
# ---------------------------------------------------------------------------
if mode == "start":
    # Start clean, so an old database or log cannot affect the result
    DB.parent.mkdir(exist_ok=True)
    DB.unlink(missing_ok=True)
    ACTION_LOG.unlink(missing_ok=True)

    # SqliteSaver stores the checkpoints in the SQLite file. The "with" block closes it cleanly.
    with SqliteSaver.from_conn_string(str(DB)) as saver:
        app = build(saver)
        for name in ("incident-1", "incident-2"):
            result = app.invoke({"incident": name}, config_for(name))
            # get_state shows where the run is. .next lists the step it will run next:
            # ("approval",) means it is waiting at the gate.
            snapshot = app.get_state(config_for(name))
            print(f"{name}: waiting at {snapshot.next} | interrupt payload: {result.get('__interrupt__')}")
    print("action log exists after start (must be False, nothing was approved yet):", ACTION_LOG.exists())
    print("Now run:  python 1c_smoke_resume.py resume")

# ---------------------------------------------------------------------------
# Process 2: a NEW process resumes the saved runs
# ---------------------------------------------------------------------------
elif mode == "resume":
    # Nothing from process 1 is in memory here. Everything the graph needs comes from the SQLite file.
    with SqliteSaver.from_conn_string(str(DB)) as saver:
        app = build(saver)
        for name, decision in (("incident-1", "approve"), ("incident-2", "reject")):
            print(f"{name}: before resume, waiting at {app.get_state(config_for(name)).next}")
            # Command(resume=...) is how the human's answer is handed back: it becomes the value
            # that interrupt() returns inside the approval step.
            app.invoke(Command(resume=decision), config_for(name))
            # An empty tuple means the run has finished
            print(f"{name}: after '{decision}', next steps = {app.get_state(config_for(name)).next}")

    lines = ACTION_LOG.read_text().splitlines() if ACTION_LOG.exists() else []
    print("actions performed:", lines)
    # Exactly one action: incident-1 (approved). incident-2 (rejected) must have done nothing.
    print("PASS" if lines == ["incident-1: create ticket for incident-1"]
          else "FAIL: expected exactly one action, for the approved incident only")

else:
    print("Usage: python 1c_smoke_resume.py start   then   python 1c_smoke_resume.py resume")
