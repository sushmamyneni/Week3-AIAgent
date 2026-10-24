# Failure-triage agent for Airflow and Spark (Week 3)

An agent that starts on its own when an Airflow DAG run or a Spark job fails. It finds the failed run, reads its log,
checks the cause against the official docs and the company's own code, database schema and change history, and
proposes a **cited** action: a ticket carrying the exact change, or a hand-off when the evidence does not support
one. A human approves before anything is written.

Built with LangChain and LangGraph (Track 2). It reuses the Week 2 hybrid-search RAG pipeline as one of its tools:
[Week2-RagPipelineProject](https://github.com/sushmamyneni/Week2-RagPipelineProject).

> **Everything here runs on synthetic data.** The "company" (Acme Retail), its code, schemas, hosts, passwords and
> the 12 failures are invented. Nothing touches a real Airflow, Spark or database system.

## How it works

```
failure appears --> watcher --> incident queue --> worker --> LangGraph agent
(simulator today;   (polls,      (SQLite, one row    (runs the     intake -> triage -> evidence loop (5 tools, cap of 8)
 Airflow/Spark      dedupes)      per failure)        graph)        -> diagnosis (model drafts, code verifies)
 API later)                                                          -> approval gate (run PAUSES here)
                                                                     -> create_ticket (only after a human approves)
              outbox (simulated Slack)  <--  notifications          escalation report (nothing to write)
              Streamlit inbox  --> approve / edit / reject --> resumes the paused run
```

**Who decides what.** The model chooses which tool to call next and drafts the diagnosis. Plain code decides
everything that must hold every time: one failure starts one run, the step cap, redaction of secrets, whether a
quote really appears in the evidence, whether a proposed change points at a line the agent actually saw, the owner
(from `CODEOWNERS`), and the fact that every write needs a human's approval. A human approves, edits or rejects.

### The tools (all read-only)
| Tool | What it gives the agent |
|---|---|
| `get_failure_log` | The failed run's log, with secrets redacted, trimmed, and wrapped as untrusted text |
| `search_docs` | Hybrid (dense + BM25) search of the Airflow and Spark docs, with a STRONG / BORDERLINE / WEAK match level |
| `search_code` | The company repository: file, line numbers and owner of the lines that match |
| `get_table_schema` | A table's columns, every change to it, and who may read or write it (schemas only, never rows) |
| `git_history` | Recent commits and the exact diff of one commit |

### Safety design
- **Reads are autonomous, every write needs approval.** The model never reaches the write: `create_ticket` is called
  only after an approval that passed code checks (reviewer name, content fingerprint, only three editable fields).
- **Citations are verified by code, not trusted.** The model quotes a numbered piece of evidence; code checks the
  quote is really there and derives the citation (`sql/orders_load.sql:6`, a commit, a migration, a docs page).
- **Confidence is made of checkable signals** (kinds of evidence that agree, docs score, allowlisted change type),
  never a number the model reports.
- **Logs, code, schemas and docs are untrusted text.** Secrets are redacted, hidden instructions to an AI are flagged
  and never obeyed or cited, tool arguments are validated, and the inbox shows untrusted text as plain text.
- **One failure, one run, one ticket.** Deduplication is in the database; a ticket is created in exclusive mode.
- **Failures are handled, not hidden:** a tool that raises is retried once and admitted in the report; the step cap,
  a missing log, an unreachable model and an agent crash all end in an escalation or a `failed` run that writes nothing.

## Setup (Python 3.12)

```powershell
uv venv .venv
.\.venv\Scripts\activate
uv pip install -r requirements.txt
copy .env.example .env        # then fill in NEBIUS_API_KEY and PINECONE_API_KEY
python 2a_make_sandbox.py     # builds the synthetic company repository (sandbox\) with a real git history
```

`search_docs` needs the docs index from Week 2: a Pinecone index named `airflow-spark-rag` and `data\chunks.jsonl`,
both produced by the Week 2 pipeline. Everything else works without them (the tests that need them are marked below).

## Try it

Three terminals, from this folder:
```powershell
python 7a_run_watcher.py                       # 1. watches incoming\ for new failures
python 7c_run_worker.py                        # 2. runs the agent on each queued failure
python 7b_simulate_failure.py inc-08 inc-11    # 3. "two jobs failed"
streamlit run 8a_inbox_app.py                  # the approval inbox: approve, edit or reject the ticket for inc-08
```
inc-08 gets a ticket (`sql/orders_load.sql:6`, owner `@acme/orders-team`) that waits for approval; inc-11 (a Snowflake
quota) is escalated with what was found. Or run one failure from the command line:
`python 4d_run_agent.py inc-08`, then `--approve NAME` or `--reject NAME`.

## Tests

Numbered files are scripts you run; plain-named files are modules you import. Every test file prints `True` per check.

| Step | Test | Needs |
|---|---|---|
| 3 tools | `3a`..`3f` | `3b` needs Pinecone and the docs data; the rest need nothing |
| 4 agent core | `4a`, `4b`, `4c` | `4b`, `4c` call the real model |
| 5 diagnosis | `5a`, `5b`, `5c`, `5d` | `5b` one docs search; `5c` part B and `5d` call the real model |
| 6 approval | `6a`, `6b` | `6b` part B calls the real model |
| 7 trigger | `7d` (no model), `7e` (real agent) | `7e` calls the real model |
| 8 inbox | `8b` | runs the page headless; no model calls |
| 9 failure cases | `9a` | scripted model stand-in: free and repeatable |

`9a_failure_tests.py` covers: an agent crash, a hostile code comment, a hostile diagnosis, a secret in a log, the
step cap, empty searches, a missing log, a tool that raises, an unreachable model, a burst of failures reported
twice, and that no ticket is written without an approval (0 unapproved writes across all scenarios).

## Results, honestly

`5d_test_all_incidents.py` runs the 12 fixtures and grades each against its answer key (`incidents\*\expected.json`,
never shown to the agent). On the last full run:

| Measure | Result |
|---|---|
| In-scope incidents correct (action, target file and line, owner) | **7 of 10**, or **8 of 10** counting inc-07's documented fallback (a retry tool is not built, so escalating is accepted) |
| Out-of-scope incidents escalated (Snowflake quota, Postgres autovacuum) | **2 of 2** |
| Crashes, unapproved writes | 0, 0 |

Read these numbers with care:
- **The model varies between runs even at temperature 0.** inc-01, inc-02 and inc-04 each flipped between a correct
  ticket and an escalation. One pass over 12 incidents is one sample, not a rate.
- **The 12 fixtures were used to develop the prompts and the policy**, so the score is optimistic for new failures.
  A fair evaluation needs held-out incidents written after the prompts are frozen.
- When a key such as `worker_memory_limit` appears on several lines of a file, nothing in code can prove the right
  line was chosen. The approval gate is the backstop.

## Limits and what comes next
- The failure source is a folder of fixtures; a real deployment would read the Airflow REST API or the Spark history
  server through the same two methods of `failure_source.py`. The Airflow `on_failure_callback` webhook is not built.
- Notifications go to an outbox folder, not Slack or email.
- No `retry_failed_task` tool, and no memory of past incidents.
- **Multi-agent next step:** the ticket is a structured contract (`tickets.py`) so that a second agent could pick up an
  approved ticket, make the change on a branch and open a pull request, with a human reviewing the PR. A ticket is
  data, never an order: its text is checked for hidden instructions before it is drafted.
- Code and schemas would be sent to the model provider; many companies could not allow that without an approved or
  self-hosted model.

## Layout
`agent.py` (graph, triage, evidence loop) · `diagnose.py` (diagnosis and action policy) · `evidence.py` (catalog and
citation check) · `tickets.py` (contract and `create_ticket`) · `approval.py` (the gate) · `tools.py`, `code_index.py`,
`docs_retrieval.py` (tools) · `failure_source.py`, `incident_queue.py`, `watcher.py`, `worker.py`, `outbox.py`
(trigger layer) · `inbox.py`, `8a_inbox_app.py` (inbox) · `incidents\` (12 fixtures) · `sandbox_src\` (the synthetic
company) · `architecture.md`, `implementation_plan.md` (design).
