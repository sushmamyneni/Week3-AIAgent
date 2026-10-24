# Implementation plan: Pipeline failure-triage agent (Week 3)

Track 2: LangChain + LangGraph. Deadlines: Builder of the Week consideration **Sunday, October 25, 2026, 11:59 pm PT**; certification **November 11, 2026**. Submission form: https://forms.gle/waBYFkkf5xxmcouV9

Working agreement: you write the code, with step-by-step guidance and commented snippets; Claude does not create or edit your files unless asked; confirm before anything paid or outward-facing.

The agent uses **two kinds of knowledge**: the official docs (what an error means) and the **enterprise code, database schemas and change history** (what the fix is in this company). It is built with a **future multi-agent design in mind** (section 8): a triage agent now, a fix agent later, with a structured ticket as the hand-off between them.

## Decisions made

| Question | Decision |
|---|---|
| How does the agent start? | **Automatically when a job fails**, not by a person typing an alert |
| Failure detection | **Polling first** (a watcher over a failure source); an Airflow **webhook** (`on_failure_callback`) as a second path if time allows |
| Notification | A **simulated outbox plus an approval inbox page**; email if time allows |
| Where the failures come from | A **failure simulator script** for now; real Airflow (and Spark) later, behind the same interface |
| Agent pattern | One agent with explicit steps now; a **second (fix) agent later**, connected through the ticket |
| Knowledge sources | Docs (Week 2 hybrid search) **plus enterprise code, database schema and git history** through read-only tools (section 7) |
| Enterprise data for the demo | A **sandbox**: a small local git repo of DAG, SQL and migration files, and a schema store with table history. Synthetic, and labelled so |
| What the agent does | It **chooses an action by failure type**: retry a transient failure (optional), file a ticket with the exact change for a code or configuration problem, or escalate (section 5) |
| Writes | Every write needs a human's approval. Nothing is merged, pushed to a main branch, or changed on a real system |
| Changing code in the repository | The ticket carries the exact change (file, line, old and new code). A branch and commit in the sandbox only if ahead on Saturday. A real GitHub pull request after Week 3 |
| The ticket | A **structured contract**, not free text (section 6) |
| Test set | 12 incidents: 7 explained by the docs, **3 that need the code or schema**, 2 out of scope |
| Memory | Session state (checkpointer) is required; the past-incident memory is optional and is cut first |
| Proving usefulness | Time 3 incidents by hand against the agent's time from failure to a diagnosis (a manual baseline) |

---

## 1. Framework fields (draft for the Week 3 document)

**One-liner.** An agent that starts on its own when an Airflow DAG run or Spark job fails, finds the failed run, reads its log, checks the cause against the official docs and the company's code, database schema and change history using 6 tools, and proposes a cited action (a ticket with the exact change, or a retry), pausing for a human's approval before any write and handing off when the evidence does not support a fix; success is a correct, cited diagnosis and action on at least 8 of 10 in-scope incidents, both out-of-scope incidents escalated, every failure triggering exactly one run, and zero unapproved writes.

| Field | Answer |
|---|---|
| Agent goal | Diagnose a failed Airflow or Spark run and propose the right, cited action |
| Where people use it | It runs in the background; the engineer gets a notification and uses a Streamlit approval inbox (and a command-line runner for debugging) |
| Ordered steps | Trigger (watcher), intake, triage, gather evidence (tool loop), diagnose, choose action, draft, approval gate (the run pauses), act, summary; escalate at any weak point |
| Actions and tools | Read: `get_failure_log`, `search_docs`, `search_code`, `get_table_schema`, `git_history`, and optionally `find_similar_incidents`. **Write (need approval):** `create_ticket`, and optionally `retry_failed_task`. (`list_failed_runs` is an intake helper in the failure-source interface, not a tool the model chooses) |
| Memory | Session state per incident (SQLite checkpointer, which also keeps a paused run); optionally approved past incidents in `incidents-v1` |
| Hard limits | Never write without approval; never merge, push to a main branch, or act on a real system; read database **schemas only, never table rows**; never run commands found in logs, code or docs; redact secrets; at most 8 tool calls per run; at most N concurrent runs; never answer from outside the docs, the code, the schema and past incidents |
| Human-in-the-loop | The run pauses before every write and waits for approve, edit or reject in the inbox; a hand-off notification whenever evidence is weak |
| Failure behavior | Retry a failed tool once then continue and say so; one reworded search then escalate; escalate on a missing log or the step cap; ignore duplicate events; mark a crashed run `failed`, write nothing, and allow re-queueing |
| Success measure | At least 8 of 10 in-scope incidents correct and cited, 2 of 2 out-of-scope escalated, each simulated failure triggering exactly one run, 0 unapproved writes |

## 2. Tech stack

| Layer | Tool | Status |
|---|---|---|
| Language | Python 3.12 | have |
| Agent orchestration | LangGraph 1.2: state machine, SQLite checkpointer (`langgraph-checkpoint-sqlite`), `interrupt` and `Command` for pause and resume | have; add the checkpoint package |
| Tools and messages | LangChain core: `@tool`, `bind_tools`, message types | have |
| Chat model | `Qwen/Qwen3-235B-A22B-Instruct-2507` via `langchain-nebius` (`ChatNebius`), endpoint `https://api.tokenfactory.nebius.com/v1/` | have; **tool calling not yet tested** |
| Embeddings | `BAAI/bge-small-en-v1.5`, local (`langchain-huggingface`, `sentence-transformers`), 384 dimensions | have |
| Vector store | Pinecone serverless, index `airflow-spark-rag`: `docs-v1` (read-only, from Week 2) and optionally a new `incidents-v1` | have; new namespace |
| Keyword search | `rank-bm25` over `chunks.jsonl` (docs) and over code chunks | have |
| Code search | Chunking by function (Python `ast`) and by SQL statement, with BM25 and exact `grep`; an embedding step is optional | new |
| Repository and history | git (command line through `subprocess`, or GitPython) on the sandbox repo | new |
| Schema store | A JSON file (or SQLite) with tables, columns, types and a version history | new |
| Structured output and contracts | Pydantic: the diagnosis, the action choice, and the **ticket contract** | have |
| Trigger layer | A polling watcher (Python standard library), a SQLite incident queue, an agent runner with a concurrency cap | new |
| Webhook (second path) | FastAPI and uvicorn, receiving Airflow's `on_failure_callback` | new, optional |
| Failure simulator | A script that adds a fixture failure and its log to the failure source | new |
| Notification | An outbox folder of notification files (simulated Slack); email via SMTP if time allows | new |
| Tracing and evals | LangSmith (key exists in `.env`, never used) | new |
| UI | Streamlit: the approval inbox | have; the page is new |
| Config and keys | PyYAML, python-dotenv, `config.yaml` (extended with agent, watcher and approval settings), `.env` | have |
| Tests | pytest (optional) for the tools, the dedupe logic, and the failure cases | optional |

Reused from Week 2 by copy: `5a_retrieval.py`, the retrieval, embedding and Pinecone settings, `data/chunks.jsonl`, and the diagnosis prompt and citation check ideas from `6a_rag_graph.py`.

## 3. Project layout (suggested)

```
Week3-AIAgent\
  config.yaml            Week 2 settings plus agent limits, watcher interval, approval rules, thresholds
  requirements.txt       Week 2 packages plus langgraph-checkpoint-sqlite (and fastapi, uvicorn if the webhook is built)
  .gitignore             .venv/, .env, data/, __pycache__/, tickets/, outbox/, incoming/, sandbox/, *.db   (create BEFORE the first commit)
  .env                   keys (never committed)
  5a_retrieval.py        copied from Week 2
  failure_source.py      MODULE: reads failures and logs (step 2)
  tools.py               MODULE: the agent's tools (step 3)
  code_index.py          MODULE: chunks and searches the sandbox code (step 3c)
  agent.py, diagnose.py  MODULES: the graph, the diagnosis and the action policy (steps 4 and 5)
  tickets.py             MODULE: the ticket model and the write tools (step 6)
  incident_queue.py      MODULE: the SQLite incident queue (step 7)
  watcher.py             MODULE: the watcher logic (step 7)
  data\chunks.jsonl      copied from Week 2
  incidents\             fixtures: 12 incidents with logs and the expected cause, sources and action, labelled synthetic
  incoming\              the failure source the watcher polls (the simulator adds failures here)
  sandbox\               the synthetic "enterprise"
    pipelines-repo\      a local git repo with its own history
      dags\              Airflow DAG files
      sql\               the SQL the DAGs run
      spark_jobs\        Spark job code and settings
      migrations\        schema migrations, one file per change
      CODEOWNERS         who owns which path (used to route tickets)
    warehouse\schema_history.json    tables, columns, types, and every version with its date
  outbox\                simulated notifications
  tickets\               structured tickets (the simulated create_ticket write)
  architecture.md, architecture.html, implementation_plan.md
```

The sandbox repository has its **own** git history, so it must not be committed inside the Week 3 repository (a repository nested in another one is not tracked properly). Add `sandbox/` to `.gitignore` and generate it with the `2a_make_sandbox.py` script, which builds the same files and the same commits every time. The script is what gets committed and shared.

**Naming convention: numbered files are scripts you run, plain-named files are modules you import.**

- A file you *run* starts with its step number, plus a letter when a step has several (`2a_make_sandbox.py`, `2b_check_failure_source.py`).
- A file that other files *import* has a plain name (`failure_source.py`). Python cannot import a name that starts with a digit with a normal `import` statement, and these modules are imported from several places, so a plain name avoids repeating a workaround everywhere.

| Scripts (you run them) | Modules (others import them) |
|---|---|
| `1a_SmokeTests.py`, `1b_smoke_tracing.py`, `1c_smoke_resume.py` | |
| `2a_make_sandbox.py`, `2b_check_failure_source.py` | `failure_source.py` |
| `3a_test_get_failure_log.py`, `3b_test_search_docs.py`, `3c_test_code_index.py`, `3d_test_search_code.py`, `3e_test_get_table_schema.py`, `3f_test_git_history.py` | `tools.py`, `code_index.py` |
| `4a_run_agent.py` | `agent.py` (the graph), `diagnose.py` (diagnosis, citation check, action policy) |
| `6a_test_approval.py` | `tickets.py` (the ticket model and the write tools) |
| `7a_run_watcher.py`, `7b_simulate_failure.py`, `7c_run_worker.py` | `incident_queue.py`, `watcher.py` |
| `8a_inbox_app.py` (Streamlit) | |
| `9a_failure_tests.py`, `10a_run_evaluation.py`, `10b_build_report.py` | |

The one exception is `5a_retrieval.py`, copied from Week 2: it is imported through `importlib` today. Under this convention the copy should be renamed (for example `docs_retrieval.py`); the Week 2 original stays untouched. Do it when convenient.

## 4. Roles and responsibilities

**The agent is responsible for:** triage (which run, which system, what kind of failure); gathering evidence by choosing which tools to call; a diagnosis with every claim cited; choosing and drafting an action; saying "I can't tell" and escalating; and reporting honestly what it checked, what it could not, and which tools failed.

**The agent must not:** fix, restart or re-run anything without approval; change code or configuration in a real system; read table rows (schemas only); answer from outside its knowledge sources; decide on its own that a write is safe; touch production credentials.

| Decision | Who | Why |
|---|---|---|
| Which tool to call next, when the evidence is enough, the wording of the diagnosis, which action fits | **The AI model** | This is the judgment part |
| One failure starts one run, the step cap, concurrency limits, redacting secrets, trimming logs, the allowlist of change types and schemas | **Plain code** | Rules that must hold every time |
| Whether a write needs approval | **Plain code** (always yes) | The model cannot talk its way past it |
| Whether the evidence supports a diagnosis | **Plain code** (a docs score threshold, or code and schema evidence) first, then the model | The Week 2 pattern, extended |
| Approve, edit or reject an action | **A human** | Anything that changes something is a human's call |
| What to do with an escalated failure | **A human** | The agent only hands over what it found |

| Action | Autonomy |
|---|---|
| Read logs, docs, code, schemas, git history | Autonomous |
| Reword and repeat a search | Autonomous, once |
| Draft a ticket | Autonomous (it is only text) |
| Create the ticket, retry a task | **Needs approval** |
| Save an incident to memory (optional) | **Needs approval** |
| Anything on a real system | **Not allowed** in this project |

## 5. Action policy: the agent chooses by failure type

| What the evidence points to | Proposed action (needs approval) |
|---|---|
| A transient failure (executor lost, a flaky connection, retries not yet exhausted) | `retry_failed_task` (optional; without it, escalate) |
| A configuration problem the docs explain, or a code or schema mismatch found in the repository | **`create_ticket`** carrying the exact change: file, line, old and new code or value, and the owner from `CODEOWNERS` |
| Unclear, conflicting, or out of scope | **Escalate**: report what was found, no write |

"Confidence" is not a number the model reports. It is made of signals the code can check: the best retrieval score, how many independent sources agree (log, docs, code, schema, git history), whether the change type is on an allowlist, whether the change validates, and the size of the change. The signals go in the ticket and decide the *kind* of output; they never remove the human's approval.

## 6. Ticket contract

The ticket is the hand-off between the triage agent and, later, a fix agent. It is a structured record, so a second agent can read fields instead of interpreting prose.

```
{
  "ticket_id": "...",
  "incident_id": "...",
  "system": "airflow | spark",
  "run": {"dag_or_app": "...", "run_id": "...", "task": "...", "failed_at": "..."},
  "root_cause": "one or two sentences",
  "evidence": [
    {"kind": "log | doc | code | schema | commit | incident", "ref": "...", "quote": "...", "citation": "docs path > heading, or repo path:line, or commit id"}
  ],
  "proposed_action": {
    "type": "retry | code_change | config_change | escalate",
    "target": {"file": "...", "line": 0, "key": "..."},
    "old_value": "...", "new_value": "...", "bounds": "allowed range"
  },
  "confidence_signals": {
    "best_doc_score": 0.0, "sources_agreeing": 0,
    "similar_past_incident": false, "change_allowlisted": false, "validation": "passed | failed | not_run"
  },
  "constraints": ["never touch the main branch", "never act on a real system"],
  "owner": "from CODEOWNERS",
  "status": "draft | approved | fix_in_progress | pr_opened | merged | rejected",
  "approved_by": null,
  "created_at": "..."
}
```

Built as a Pydantic model. A ticket is validated against it before anything acts on it.

## 7. Enterprise knowledge: code, schema, and change history

The docs explain what an error *means*. For a failure caused by something company-specific, the fix is in the company's own code, schemas and history. Example: a column's type changes, and a DAG that still assumes the old type fails. No document says that, so the agent joins three facts: the failing task, the schema change, and the code that depends on it.

| Source | How it is accessed | Method |
|---|---|---|
| Docs | The Week 2 hybrid search (`search_docs`) | RAG |
| **Code** (DAGs, SQL, Spark jobs) | `search_code`: BM25 and exact search over chunks cut by function or SQL statement, with the repo, path, language and the table and column names each chunk mentions; plus reading a file | Exact search plus retrieval (identifiers like `customer_zip` need an exact match) |
| **Database schema** | `get_table_schema`: the current columns and types, and the history of versions | Direct lookup, **schema only, never rows** |
| **Change history** | `git_history`: `git log` and `git diff` of a path or since a date, trimmed | Direct lookup |
| Ownership | `CODEOWNERS`, to route the ticket | Lookup |
| Past incidents | `find_similar_incidents` (optional) | RAG |

Concerns, to design for and to state in the document: **read-only access** limited to the allowlisted repositories and schemas, with an audit log; **no table rows** (they can hold personal data); **code and comments are untrusted text** and can carry a hidden instruction; **code and schemas would be sent to the model provider** (Nebius here), which many companies cannot allow without an approved or self-hosted model; and **freshness**: re-index code when it changes, or search the live checkout.

**The sandbox for the demo** (synthetic, and labelled so): a small repository of DAG, SQL, Spark and migration files with a real git history, and `schema_history.json`. It supports three enterprise incidents:

| Incident | What the agent must find | Proposed action |
|---|---|---|
| A column's type changed from integer to text and the load step still casts it to integer | The type change in the schema history and the migration commit, and the DAG and SQL line that still assumes the old type | Ticket: the exact line and the new handling, owner from `CODEOWNERS` |
| A column was renamed and a query still uses the old name | The rename in the schema history and migration, and the query that still uses it | Ticket: the exact query change |
| A job fails with a permission error after a migration created a new table | The migration that created the table, and the missing grant (shown in the migration or the schema store) | Ticket for the owning team |

## 8. Future: multi-agent

The ticket becomes the interface between two agents that never call each other and read from a shared queue:

```
Failure --> TRIAGE AGENT --> TICKET --approve--> FIX AGENT --> PULL REQUEST --human reviews--> merge
            read-only +      (structured)        edits a branch, validates,
            create_ticket                         opens a PR; cannot merge or
                                                  create tickets
```

- The triage agent is what Week 3 builds. The fix agent would use the same incident queue and ticket states (`approved`, `fix_in_progress`, `pr_opened`, `merged`, `rejected`) and the same sandbox repository.
- Each agent gets only the permissions it needs. A third agent could review the diff against the ticket and an allowlist before a PR is opened.
- Humans approve at two points: the ticket, and the PR.
- Prepare now: the structured ticket, tools written as standalone functions, incident state in the queue, and a rule that the fix agent validates a ticket against its schema and an allowlist before acting. A log, a doc or a code comment can carry a hidden instruction into a ticket, so ticket text is never trusted as an order.
- Harder later: ticket quality, limiting bounces between agents, evaluating the whole chain, cost and time.

## 9. Steps

The timeline is tight: the enterprise sandbox adds about half a day, so the steps are spread over three days and the optional items are cut.

| # | Step | What you build | Check | When |
|---|---|---|---|---|
| 0 | **Setup** | `.gitignore` first (`.env` and `data/` are already in the folder), update `requirements.txt`, install into this folder's `.venv`, make sure the prompt shows the Week 3 environment. In the **Week 2** repo, `git tag week2-submission` from inside `week2-rag` | `git status` does not list `.env` or `data/`; the packages import | Fri |
| 1 | **Smoke tests** | Does Qwen3 return tool calls through `bind_tools`; a LangSmith trace appears; a toy graph with an interrupt and a resume works with the SQLite checkpointer, including resuming from a new process | A tool call is parsed; a trace shows in LangSmith; a paused run resumes | Fri |
| 2 | **Sandbox and incident fixtures** | The sandbox repository and `schema_history.json` (script that rebuilds them). 12 incidents with logs and the expected cause, sources and action: **7 explained by the docs** (Airflow: killed by signal or out of memory, retries exhausted, sensor timeout, state changed externally; Spark: executor lost, shuffle partitions or `maxFailures`, YARN submit; include one transient failure that should be retried), **3 needing the code or schema** (section 7), and **2 out of scope** (Snowflake, PostgreSQL). The failure source (`incoming\`) sits behind a small interface | The sandbox builds; 12 incidents load; each expected source exists | Fri |
| 3 | **Tools** | `get_failure_log` (trim, redact), `search_docs` (wraps Week 2 retrieval), `search_code` (chunk by function and SQL statement, BM25 and exact search), `get_table_schema`, `git_history`; each tested alone, including a failing case | Each tool passes its test | Fri to Sat |
| 4 | **Agent core** | State, the intake and triage nodes, the evidence loop, a step cap of 8, tool-error handling (retry once, carry on); the input is a structured failure event | One docs-based and one code-based incident run end to end | Sat |
| 5 | **Diagnose, choose action, escalate** | Structured diagnosis (cause, evidence, cited fix, answerable), the citation check from Week 2 extended to code, schema and commits, the action policy, the Pydantic ticket model, the escalation rule | Each fixture gets the expected action; out-of-scope incidents escalate | Sat |
| 6 | **Approval gate and write tools** | `interrupt` before every write; approve, edit, reject; `create_ticket` writes a validated ticket to `tickets\`; `retry_failed_task` (optional) marks the run as retried in the failure source | Reject writes nothing; approve performs exactly one action | Sat |
| 7 | **Trigger layer** | The incident queue (SQLite table with states), the watcher (polls `incoming\`, deduplicates on system and run id), the agent runner (one run per new incident, a cap on concurrent runs), the notification outbox, and the failure simulator script | Running the simulator starts exactly one agent run without anyone typing; running it twice for the same run starts none | Sat |
| 8 | **Approval inbox** | A Streamlit page listing incidents awaiting approval with the diagnosis, proposed action and cited sources, and Approve, Edit and Reject buttons that resume the run | A failure simulated in one terminal appears in the inbox; approving it performs the action | Sat to Sun |
| 9 | **Failure tests** | Scripted cases: a tool raises, an empty search, a missing log, the step cap, an instruction hidden in a log or in a code comment, a secret in a log, a duplicate event, a burst of failures, an agent crash | All scripted tests pass | Sun |
| 10 | **Evaluation** | Run all 12 through the real trigger path; record completion, the action chosen against the expected one, escalation correctness, runs per failure (must be exactly 1), unapproved writes (must be 0), time from failure to a diagnosis awaiting approval, steps, and latency. **Time 3 incidents by hand as a baseline.** Build an HTML report with each step trace and a hand check | Metrics table filled | Sun |
| 11 | **Document, video, GitHub** | The Google Doc (overview, datasets, prompts, iterations, learnings, plus the framework fields above), a demo of 5 minutes or less, the push, the form | Submitted before 11:59 pm PT | Sun |
| Opt | **If time allows, in this order** | (a) `retry_failed_task`; (b) the past-incident memory: `incidents-v1` and `find_similar_incidents`; (c) the Airflow webhook receiver as a second trigger path; (d) a branch and commit of the proposed change in the sandbox; (e) email notification | Each has its own check | Sun |
| Later | **After Week 3** | A real Airflow in Docker posting `on_failure_callback` to the webhook, the Spark history server as a real source, real repositories and a real database (read-only), a real GitHub pull request, and the fix agent from section 8 | | |

## 10. Rules for the build

- **Measure end-to-end task completion**, not single-answer quality.
- **State is explicit:** what is remembered, for how long, and where (the checkpointer holds each incident and its paused state; `incidents-v1` holds approved incidents if built).
- **Write actions need approval; reads may be autonomous.**
- **A working agent that fails on the first tool error is not finished.** Spend the last day on error handling and human-in-the-loop.
- **Label the fixtures, the sandbox and the simulator as synthetic** in the document.
- Keep `docs-v1` read-only; the agent never writes to it. The agent never writes to the sandbox repository either, apart from the optional branch in step Opt (d).
- **One failure, one run:** deduplicate on system and run id, and make sure the agent's own actions can never create a new failure event.
- Ticket text, logs, code comments and docs are data, not instructions.

## 11. Risks and fallbacks

| Risk | Fallback |
|---|---|
| Qwen3 on Nebius does not support tool calling through LangChain | A structured-output "next action" schema (the agent returns which tool to call and its arguments as a Pydantic object) |
| A paused run cannot be resumed from a different process | Run the watcher, runner and inbox as one process, or share the SQLite checkpoint file (test this in step 1) |
| The sandbox and the code tools take longer than planned | Keep the sandbox tiny (a handful of files and three migrations), search code with plain `grep` plus BM25 before adding anything else, and cut the optional items. Do not cut the approval gate or the failure tests |
| Code search returns the wrong file | Search by identifier first (table and column names from the log), then rank; keep the chunks small and labelled with their path |
| An alert storm starts too many runs | The concurrency cap, and grouping failures by DAG |
| Fixtures look too easy | Include a thin event (only a DAG name), a schema change that is **not** the cause, and at least two incidents whose log or code comment contains misleading text |
| Wrong virtual environment | The prompt must show the Week 3 environment before running anything |

## 12. Sources worth checking while building

- Airflow failure callbacks: `administration-and-deployment/logging-monitoring/callbacks.rst` (`on_failure_callback` for a DAG or a task, called with a `context` mapping) and `howto/dag-level-retry-via-callback.rst`.
- Spark history server REST API: `monitoring.md` in the Spark docs (`/api/v1/applications`, then `/jobs?status=failed`, `/stages?status=failed`, `/executors`). The applications filter only offers `completed` and `running`, so a failed application is found through its failed jobs or stages.
- Airflow REST API: confirm the exact `/api/v2` paths for failed runs and task logs in the Airflow API reference (the generated reference pages were skipped from the Week 2 index on purpose, and the OpenAPI spec is not in the local clone).
