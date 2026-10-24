# Step 1b smoke test: does LangSmith tracing work?
#
# Tracing records each step of a run (every model call and tool call) in LangSmith, so we can
# see what the agent did and why. This script makes one small traced run and then asks LangSmith
# whether it arrived. WARNING: it uploads the prompt and answer text to the LangSmith cloud.
# Here that is only fake data.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 1b_smoke_tracing.py
import os
import time

import yaml
from dotenv import load_dotenv

# Load the keys from .env FIRST. LangSmith reads its settings from environment variables when
# the first traced call happens, so they must be set before any model call.
load_dotenv()

# Turn tracing on and name the project the runs will appear under. setdefault keeps anything
# already set in .env, so these two lines only fill in what is missing.
os.environ.setdefault("LANGSMITH_TRACING", "true")
os.environ.setdefault("LANGSMITH_PROJECT", "week3-agent")
# If your LangSmith account is in the EU region, also set
#   LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
# (put it in .env). Without it, traces go to the default US endpoint.

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_core.tracers.langchain import wait_for_all_tracers
from langchain_nebius import ChatNebius
from langsmith import Client, traceable

cfg = yaml.safe_load(open("config.yaml"))
llm = ChatNebius(
    model_name=cfg["chat"]["model"],
    temperature=0,
    max_tokens=200,
    nebius_api_base=cfg["nebius"]["api_base"],
    nebius_api_key=os.environ["NEBIUS_API_KEY"],
)

# The same fake tool as in step 1a. Tool calls show up in the trace as their own steps.
@tool
def get_table_schema(table: str) -> str:
    """Return the current columns and types of a database table."""
    return f"{table}(id INTEGER, customer_zip VARCHAR(10), total NUMERIC)"

# @traceable makes this whole function one parent step ("a span") in the trace, named
# smoke_test_triage. Every model call and tool call made inside it appears underneath it,
# as children. Without the decorator, each model call would show up as a separate, unrelated run.
@traceable(name="smoke_test_triage")
def triage_demo(question: str) -> str:
    # Calls made through LangChain objects (the model, the tool) are traced automatically
    reply = llm.bind_tools([get_table_schema]).invoke([HumanMessage(content=question)])
    if reply.tool_calls:
        call = reply.tool_calls[0]
        return get_table_schema.invoke(call["args"])        # traced as a child step
    return reply.content

print("Running one traced call...")
print("  result:", triage_demo("What does the orders table look like?")[:120])

# Traces are sent in the background. This waits until they have all been sent, otherwise the
# script could end before the upload finishes and nothing would arrive.
wait_for_all_tracers()

# Ask LangSmith what it has received for this project. The runs can take a few seconds to
# show up, so check a few times before giving up.
client = Client()
project = os.environ["LANGSMITH_PROJECT"]
found = []
for attempt in range(6):
    found = list(client.list_runs(project_name=project, limit=10))
    if any(run.name == "smoke_test_triage" for run in found):
        break
    time.sleep(2)

print(f"Runs in project '{project}':")
for run in found[:6]:
    print(f"  {run.name:<22} status={run.status}")
print("PASS" if any(run.name == "smoke_test_triage" for run in found)
      else "FAIL: the trace did not arrive (check the key, the region, and LANGSMITH_TRACING)")
