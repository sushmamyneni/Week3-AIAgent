# Step 1a smoke test: can the chat model call tools through LangChain?
#
# The agent depends on this. The model must be able to answer "I want to call the tool
# get_table_schema with table='orders'" instead of plain text. This script checks three things:
#   Check 1: given a question that needs a tool, the model asks for the right tool with the right arguments
#   Check 2: given the tool's result, the model uses it in its final answer
#   Check 3: given a question that needs no tool, the model does NOT ask for one (it decides)
#
# Usage (run from the Week3-AIAgent folder with the Week 3 environment active):
#   python 1a_smoke_tests.py
import os

import yaml
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_nebius import ChatNebius

# ---------------------------------------------------------------------------
# Setup: keys, settings, and the model
# ---------------------------------------------------------------------------

# Copy the keys from .env into environment variables. The key is read from the
# environment and is never printed.
load_dotenv()

# Settings come from config.yaml (copied from Week 2), so no model name is hardcoded here
cfg = yaml.safe_load(open("config.yaml"))

# The same chat model and endpoint as Week 2. Temperature 0 makes the answers as repeatable as
# possible, which helps when we are checking behavior. max_tokens is generous because a tool
# request and a final answer are both short.
llm = ChatNebius(
    model_name=cfg["chat"]["model"],
    temperature=0,
    max_tokens=400,
    nebius_api_base=cfg["nebius"]["api_base"],
    nebius_api_key=os.environ["NEBIUS_API_KEY"],
)

# ---------------------------------------------------------------------------
# A fake tool
# ---------------------------------------------------------------------------

# The @tool decorator turns a normal function into something the model can be told about.
# LangChain builds the tool's description from three things the model will read:
#   - the function name           (get_table_schema)
#   - the argument names and types (table: str)
#   - the docstring below, which is how the model decides WHEN to use it. Write it like
#     an instruction, because it is one.
# The body is fake data for now. In the real project it will read the schema store.
@tool
def get_table_schema(table: str) -> str:
    """Return the current columns and types of a database table, and when a column's type last
    changed. Use this when a failure mentions a table or a column."""
    schemas = {
        "orders": "orders(id INTEGER, customer_zip VARCHAR(10), total NUMERIC). "
                  "customer_zip changed from INTEGER to VARCHAR(10) on 2026-10-20 (migration 0042).",
    }
    return schemas.get(table, f"No table named {table}.")

# A lookup by name, so we can run whichever tool the model asks for
tools_by_name = {"get_table_schema": get_table_schema}

# bind_tools tells the model that these tools exist. It does not run anything. The model
# can now answer either with text or with a request to call one of the tools.
llm_with_tools = llm.bind_tools([get_table_schema])

system = SystemMessage(content="You are a data pipeline troubleshooting assistant. Use tools when they help.")

# ---------------------------------------------------------------------------
# Check 1: the model asks for the right tool with the right arguments
# ---------------------------------------------------------------------------
print("CHECK 1: does the model request a tool?")
try:
    question = HumanMessage(content="The load of the orders table failed with: invalid input syntax for "
                                    "type integer: 'N/A'. What does the schema of orders look like now?")
    messages = [system, question]

    # The reply is an AIMessage. If the model wants a tool, the request is in .tool_calls,
    # a list like [{"name": "get_table_schema", "args": {"table": "orders"}, "id": "..."}]
    reply = llm_with_tools.invoke(messages)
    print("  tool_calls:", [(c["name"], c["args"]) for c in reply.tool_calls])
    print("  PASS" if reply.tool_calls and reply.tool_calls[0]["name"] == "get_table_schema"
          and reply.tool_calls[0]["args"].get("table") == "orders" else "  FAIL: no tool call, or the wrong one")

    # -----------------------------------------------------------------------
    # Check 2: the model uses the tool's result
    # -----------------------------------------------------------------------
    print("CHECK 2: does the model use the tool result?")
    # The conversation so far must be sent back in order: the question, then the model's own
    # request, then one ToolMessage per request holding the result. Each ToolMessage carries
    # the tool_call_id of the request it answers, which is how the model matches them up.
    messages.append(reply)
    for call in reply.tool_calls:
        result = tools_by_name[call["name"]].invoke(call["args"])   # run the real function
        messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
    final = llm_with_tools.invoke(messages)
    print("  final answer:", final.content[:300])
    print("  PASS" if "VARCHAR" in final.content else "  FAIL: the answer does not mention the new type")
except Exception as e:
    # Print the error type and the start of the message, never the whole traceback.
    # A message like "tools are not supported" would be the key finding here.
    print("  ERROR:", type(e).__name__, str(e)[:300])

# ---------------------------------------------------------------------------
# Check 3: the model does not call a tool when none is needed
# ---------------------------------------------------------------------------
print("CHECK 3: does the model skip the tool when it is not needed?")
try:
    reply = llm_with_tools.invoke([system, HumanMessage(content="Say hello in one short sentence.")])
    print("  tool_calls:", reply.tool_calls, "| text:", reply.content[:80])
    print("  PASS" if not reply.tool_calls else "  FAIL: it called a tool for no reason")
except Exception as e:
    print("  ERROR:", type(e).__name__, str(e)[:300])
