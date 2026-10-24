# Sandbox seed (synthetic)

A made-up company, "Acme Retail", used as the agent's "enterprise". Nothing here is real: the people, hosts,
tables, passwords and tokens are invented, and the code is not meant to run.

`commits/` holds one folder per git commit, in order (`01_initial`, `02_...`). Each folder has a `COMMIT.json`
(message, author, date) and the files that commit adds or replaces. `2a_make_sandbox.py` copies them in order
into `sandbox/pipelines-repo` and commits each one with its fixed date, so the history is the same every time.

`warehouse/schema_history.json` is the schema store: every table's columns, every version, and who may read or
write it. It is not part of the git repository.
