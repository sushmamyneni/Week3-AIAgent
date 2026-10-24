# The evidence catalog and the citation check.
#
# The model must not be trusted to cite honestly. So the model only says WHICH evidence a fact comes from
# (E3) and quotes it. Plain code then checks that the quote really appears in that evidence, and works out
# the citation (the file and line, the commit, the migration, the docs page) from where the quote was found.
# A fact that cannot be verified is dropped. This is the Week 2 citation check, extended from docs to code,
# schemas and commits.
#
#   E1, E2, ...  the tool calls of the run, in order (E1 is the first call)
#   L            the cleaned failure log
import re

from tickets import Evidence

# The kind of evidence each tool produces
TOOL_KIND = {"search_docs": "doc", "search_code": "code", "get_table_schema": "schema", "git_history": "commit"}

# A quote shorter than this proves nothing ("0", "id" appear everywhere)
MIN_QUOTE_CHARS = 6

# Collapse every run of whitespace to one space, so a quote still matches when a docs paragraph wraps lines
def squash(text: str) -> str:
    return " ".join(text.split())

# ---------------------------------------------------------------------------
# The catalog: one entry per piece of evidence, with its id
# ---------------------------------------------------------------------------

# Returns {"L": {...}, "E1": {...}, ...}. Each entry has the kind, the tool, the arguments, whether the tool
# worked, and the text. Failed tool calls are kept (so the numbering matches the run) but cannot be cited.
def build_catalog(state: dict) -> dict:
    catalog = {"L": {"id": "L", "kind": "log", "tool": "get_failure_log", "args": {}, "ok": True,
                     "text": state["log_text"]}}
    for n, e in enumerate(state["evidence"], start=1):
        catalog[f"E{n}"] = {"id": f"E{n}", "kind": TOOL_KIND[e["tool"]], "tool": e["tool"], "args": e["args"],
                            "ok": e["ok"], "text": e["result"]}
    return catalog

# The catalog as text for the model: every entry labelled with its id, so the model can cite it
def render_catalog(catalog: dict, max_chars_each: int = 3500) -> str:
    parts = []
    for entry in catalog.values():
        label = "the failure log" if entry["id"] == "L" else \
            f"{entry['tool']}({', '.join(f'{k}={v!r}' for k, v in entry['args'].items())})" + \
            ("" if entry["ok"] else "  [FAILED: cannot be cited]")
        text = entry["text"] if len(entry["text"]) <= max_chars_each else entry["text"][:max_chars_each] + "\n... [cut] ..."
        parts.append(f"=== {entry['id']}: {label}\n{text}")
    return "\n\n".join(parts)

# ---------------------------------------------------------------------------
# Reading each tool's output: the same formats the tools in tools.py print
# ---------------------------------------------------------------------------

CODE_HEADER = re.compile(r"^(\S+?):(\d+)-(\d+) \| owner: (.+?) \| (.+)$")      # sql/orders_load.sql:1-10 | owner: @acme/x | sql
CODE_LINE = re.compile(r"^(?:>>|  )\s*(\d+)  (.*)$")                           # ">>   6  the code" or "     7  the code"

# The code hits as blocks: path, first and last line, owner, and the numbered lines
def parse_code(text: str) -> list:
    blocks = []
    for line in text.splitlines():
        header = CODE_HEADER.match(line)
        if header:
            blocks.append({"path": header.group(1), "start": int(header.group(2)), "end": int(header.group(3)),
                           "owner": header.group(4), "lines": []})
        elif blocks:
            numbered = CODE_LINE.match(line)
            if numbered:
                blocks[-1]["lines"].append((int(numbered.group(1)), numbered.group(2)))
    return blocks

DOC_HEADER = re.compile(r"^\[(\d+)\] (airflow|spark) \| ([^|]+?) \| (.*)$")    # [1] airflow | troubleshooting.rst | Troubleshooting > ...

# The docs passages: system, path, heading and the whole body as one line of text
def parse_docs(text: str) -> list:
    passages = []
    for line in text.splitlines():
        header = DOC_HEADER.match(line)
        if header:
            passages.append({"system": header.group(2), "path": header.group(3), "heading": header.group(4), "body": []})
        elif passages and not line.startswith("</docs_passages>"):
            passages[-1]["body"].append(line)
    for p in passages:
        p["body"] = squash(" ".join(p["body"]))
    return passages

# ---------------------------------------------------------------------------
# The check: does this quote appear in this evidence, and where?
# ---------------------------------------------------------------------------

# Returns (evidence, reason). On success evidence is a dict that fits tickets.Evidence and reason is "".
# On failure evidence is None and reason says why, so the report can show what was dropped.
def check_quote(catalog: dict, ref: str, quote: str):
    quote = quote.strip().strip("\"'`")
    ref = ref.strip()
    entry = catalog.get(ref)
    if entry is None:
        return None, f"no evidence {ref!r} in this run"
    if not entry["ok"]:
        return None, f"{ref} is a tool call that failed"
    if len(squash(quote)) < MIN_QUOTE_CHARS:
        return None, f"the quote is too short to prove anything ({quote!r})"
    kind = entry["kind"]
    if kind != "doc" and "\n" in quote:
        return None, "quote one line only"

    citation = _locate(entry, quote)
    if citation is None:
        return None, f"the quote does not appear in {ref}"
    return {"kind": kind, "ref": ref, "quote": squash(quote), "citation": citation}, ""

# Where in the evidence does the quote appear? Returns the citation, or None if it does not appear.
def _locate(entry: dict, quote: str):
    kind, text, want = entry["kind"], entry["text"], squash(quote)

    if kind == "doc":
        for p in parse_docs(text):                      # a docs quote may wrap over lines, so it is matched on the body
            if want in p["body"] or want in squash(p["heading"]):
                return f"{p['system']} docs: {p['path']} > {p['heading']}"
        return None

    if kind == "code":
        for block in parse_code(text):
            for number, code in block["lines"]:
                if want in squash(code):
                    return f"{block['path']}:{number}"
        return None

    if kind == "log":
        return "task log" if any(want in squash(line) for line in text.splitlines()) else None

    # Schemas and commits: walk the lines, keeping track of which section or commit we are in
    table, section, commit, diff_file = "", "", "", ""
    for line in text.splitlines():
        if kind == "schema":
            if line.startswith("Schema for "):
                table = line.split()[2]
            elif line.startswith("Columns now"):
                section = "current columns"
            elif line.startswith("Changes"):
                section = "history"
            elif line.startswith("Privileges"):
                section = "privileges"
            if want in squash(line):
                if section == "history":
                    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})\s+migration (\S+)", line)
                    if m:
                        return f"schema {table}: migration {m.group(2)}, {m.group(1)}"
                if section:
                    return f"schema {table}: {section}"
        else:                                           # commit
            m = re.match(r"commit ([0-9a-f]{7,40})", line)
            if m:
                commit, diff_file = m.group(1), ""
            m = re.match(r"diff --git a/(\S+) b/", line)
            if m:
                diff_file = m.group(1)
            if want in squash(line) and commit:
                return f"commit {commit}" + (f", {diff_file}" if diff_file else "")
    return None

# ---------------------------------------------------------------------------
# Facts the action policy needs, read from the catalog
# ---------------------------------------------------------------------------

# The text of a line the agent actually saw in a code search, or None. A proposed change must point at a
# line that was really seen, and its old value must be in that line.
def code_line(catalog: dict, path: str, number: int):
    for entry in catalog.values():
        if entry["kind"] == "code" and entry["ok"]:
            for block in parse_code(entry["text"]):
                if block["path"] == path:
                    for n, code in block["lines"]:
                        if n == number:
                            return code
    return None

# Every line of a file that the code searches showed, as (number, text), without duplicates. Used to tell an
# edit of an existing setting from the addition of a new one.
def file_lines(catalog: dict, path: str) -> list:
    seen = {}
    for entry in catalog.values():
        if entry["kind"] == "code" and entry["ok"]:
            for block in parse_code(entry["text"]):
                if block["path"] == path:
                    seen.update(dict(block["lines"]))
    return sorted(seen.items())

# The owner of a file, as the code search printed it (it comes from CODEOWNERS), or None
def code_owner(catalog: dict, path: str):
    for entry in catalog.values():
        if entry["kind"] == "code" and entry["ok"]:
            for block in parse_code(entry["text"]):
                if block["path"] == path:
                    return block["owner"]
    return None

# The best docs similarity score among the docs searches of the run, or None if the docs were not used
def best_doc_score(catalog: dict):
    scores = [float(m.group(1)) for entry in catalog.values() if entry["kind"] == "doc" and entry["ok"]
              for m in re.finditer(r"Best match score (\d\.\d+)", entry["text"])]
    return max(scores) if scores else None
