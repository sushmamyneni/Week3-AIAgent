# Step 4c test: the evidence loop. Part A uses a scripted fake model and stub tools (free and fast) to check
# the rules that plain code must enforce: the step cap, retries, refused arguments, duplicates, a failed model.
# Part B runs the real model and the real tools on three incidents (about 20 small model calls; the first
# search_docs call loads the embedding model, which takes up to a minute).
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 4c_test_evidence_loop.py
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

import agent
import tools
from failure_source import FixtureFailureSource

# ---------------------------------------------------------------------------
# Part A: fake model, stub tools
# ---------------------------------------------------------------------------

# A model that replays a script: each call returns the next prepared reply. A reply that is an exception
# is raised instead, to imitate a network failure. bind_tools returns the model itself.
class ScriptedModel:
    def __init__(self, script):
        self.script, self.calls = list(script), 0
    def bind_tools(self, tools):
        return self
    def invoke(self, messages):
        self.calls += 1
        reply = self.script.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

# An AIMessage that asks for tool calls, or (with no calls) finishes with a closing note
def ask(*calls, text=""):
    return AIMessage(content=text, tool_calls=[{"name": n, "args": a, "id": f"call-{i}-{n}", "type": "tool_call"}
                                               for i, (n, a) in enumerate(calls)])

counters = {"flaky": 0, "strict": 0, "broken": 0}

@tool
def echo(text: str) -> str:
    """Return the text."""
    return f"echo: {text}"

@tool
def flaky(text: str) -> str:
    """Fails on the first call, works on the second."""
    counters["flaky"] += 1
    if counters["flaky"] == 1:
        raise RuntimeError("temporary glitch")
    return "flaky worked"

@tool
def strict(text: str) -> str:
    """Always refuses its argument, like a tool given a bad date."""
    counters["strict"] += 1
    raise ValueError("text must be a date")

@tool
def broken(text: str) -> str:
    """Always crashes."""
    counters["broken"] += 1
    raise RuntimeError("tool crashed")

stubs = [echo, flaky, strict, broken]

# Run the loop (gather <-> run_tools) on a prepared state. This is the same wiring as agent.build_graph,
# without intake and triage. A small cap can be set for one run; it is restored afterwards.
def run_loop(script, cap=None):
    saved = agent.agent_cfg["max_tool_calls"]
    if cap:
        agent.agent_cfg["max_tool_calls"] = cap
    try:
        model = ScriptedModel(script)
        graph = StateGraph(agent.AgentState)
        graph.add_node("gather", agent.make_gather(model, stubs))
        graph.add_node("run_tools", agent.make_run_tools({t.name: t for t in stubs}))
        graph.add_edge(START, "gather")
        graph.add_conditional_edges("gather", agent.route_after_gather, {"tools": "run_tools", "end": END})
        graph.add_edge("run_tools", "gather")
        state = {"incident": {"system": "airflow", "dag_id": "d", "task_id": "t", "failed_at": "2026-10-22"},
                 "triage": {"failure_kind": "unknown", "error_summary": "x", "identifiers": []},
                 "log_text": "<untrusted_log>\nboom\n</untrusted_log>",
                 "messages": [], "tool_calls_used": 0, "evidence": [], "notes": [], "status": "running"}
        return graph.compile().invoke(state, {"recursion_limit": 60}), model
    finally:
        agent.agent_cfg["max_tool_calls"] = saved

print("TEST 1: a normal run: one tool call, then the closing note")
out, _ = run_loop([ask(("echo", {"text": "hi"})), ask(text="all done")])
print("  one call counted:", out["tool_calls_used"] == 1)
print("  evidence recorded:", out["evidence"] == [{"tool": "echo", "args": {"text": "hi"}, "ok": True, "result": "echo: hi"}])
print("  closing note kept:", out["evidence_summary"] == "all done" and out["status"] == "running")

print("TEST 2: the step cap hands the run to a human")
cap = agent.agent_cfg["max_tool_calls"]
out, _ = run_loop([ask(("echo", {"text": str(i)})) for i in range(cap + 1)])
print(f"  stopped at {cap} calls:", out["tool_calls_used"] == cap)
print("  escalated with a reason:", out["status"] == "escalated" and "step cap" in out["escalation_reason"])

print("TEST 3: several calls in one reply that go past the cap: the extra ones are skipped")
out, _ = run_loop([ask(("echo", {"text": "a"}), ("echo", {"text": "b"}), ("echo", {"text": "c"})), ask(text="done")], cap=2)
print("  two run, one skipped:", out["tool_calls_used"] == 2 and len(out["evidence"]) == 2)
print("  skipped call answered:", any("Skipped" in m.content for m in out["messages"] if m.type == "tool"))
print("  admitted in the notes:", any("skipped" in n for n in out["notes"]))

print("TEST 4: an identical repeated call is not run again, but still counts")
out, _ = run_loop([ask(("echo", {"text": "x"})), ask(("echo", {"text": "x"})), ask(text="done")])
print("  run once:", len(out["evidence"]) == 1 and out["tool_calls_used"] == 2)
print("  model told so:", any("already ran" in m.content for m in out["messages"] if m.type == "tool"))

print("TEST 5: a tool that fails once is retried, and the report says so")
out, _ = run_loop([ask(("flaky", {"text": "x"})), ask(text="done")])
print("  worked on the retry:", out["evidence"][0]["ok"] and out["evidence"][0]["result"] == "flaky worked")
print("  noted:", out["notes"] == ["flaky failed once and worked on a retry."])

print("TEST 6: a tool that keeps failing does not stop the run")
out, _ = run_loop([ask(("broken", {"text": "x"})), ask(text="done")])
print("  tried twice:", counters["broken"] == 2)
print("  recorded as failed:", out["evidence"][0]["ok"] is False)
print("  admitted in the notes:", len(out["notes"]) == 1 and "failed after a retry" in out["notes"][0])
print("  loop carried on:", out["status"] == "running" and out["evidence_summary"] == "done")

print("TEST 7: refused arguments are not retried, and the model is told why")
out, _ = run_loop([ask(("strict", {"text": "yesterday"})), ask(text="done")])
print("  called once:", counters["strict"] == 1)
print("  model told:", any("refused" in m.content for m in out["messages"] if m.type == "tool"))

print("TEST 8: an unknown tool name is answered, not a crash")
out, _ = run_loop([ask(("delete_everything", {"x": "1"})), ask(text="done")])
print("  answered:", any("no tool named" in m.content for m in out["messages"] if m.type == "tool"))
print("  nothing recorded as evidence:", out["evidence"] == [])

print("TEST 9: a model that cannot be reached escalates after one retry")
out, model = run_loop([ConnectionError("down"), ConnectionError("down")])
print("  tried twice:", model.calls == 1 + agent.agent_cfg["tool_retries"])
print("  escalated with a reason:", out["status"] == "escalated" and "could not be reached" in out["escalation_reason"])

# ---------------------------------------------------------------------------
# Part B: the real model and the real tools
# ---------------------------------------------------------------------------
source = FixtureFailureSource("incidents")
tools_by_name = {t.name: t for t in tools.build_tools(source)}
app = agent.build_graph(source, tools_by_name, agent.make_llm(900))

def investigate(incident_id):
    event = next(e for e in source.list_failures() if e["incident_id"] == incident_id)
    out = app.invoke({"event": event}, {"recursion_limit": 50})
    print(f"  {incident_id}: {out['tool_calls_used']} tool calls:",
          ", ".join(f"{e['tool']}({', '.join(str(v) for v in e['args'].values())})" for e in out["evidence"]))
    return out

def result_of(out, tool_name):
    return " ".join(e["result"] for e in out["evidence"] if e["tool"] == tool_name and e["ok"])

print("TEST 10: the real agent gathers the right evidence for a schema failure (inc-08)")
out = investigate("inc-08")
print("  finished within the cap:", out["status"] == "running" and 0 < out["tool_calls_used"] <= agent.agent_cfg["max_tool_calls"])
print("  schema shows the type change:", "VARCHAR(10)" in result_of(out, "get_table_schema"))
print("  code search found the cast:", "customer_zip::INTEGER" in result_of(out, "search_code"))
print("  closing note written:", bool(out.get("evidence_summary")))

print("TEST 11: a configuration failure uses the docs and the code (inc-01)")
out = investigate("inc-01")
used = {e["tool"] for e in out["evidence"]}
print("  docs searched:", "search_docs" in used)
print("  code searched:", "search_code" in used)
print("  finished within the cap:", out["status"] == "running" and out["tool_calls_used"] <= agent.agent_cfg["max_tool_calls"])

print("TEST 12: an out-of-scope failure ends with a plain note, not a guess (inc-11)")
out = investigate("inc-11")
print("  finished within the cap:", out["status"] == "running" and out["tool_calls_used"] <= agent.agent_cfg["max_tool_calls"])
print("  note mentions Snowflake:", "snowflake" in out.get("evidence_summary", "").lower())
print("  closing note:", out.get("evidence_summary", "")[:300].replace("\n", " "))