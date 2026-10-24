# The code index: splits the sandbox repository into chunks and searches them.
#
# A chunk is a piece of a file with its path and its line numbers, so a ticket can say "sql/orders_load.sql,
# line 6". Small files are kept whole. A long Python file is split by function and class (using Python's own
# parser), and a long SQL file by statement, so a hit is a unit that means something.
#
# Search is keyword search (BM25) with a boost for exact identifiers: a name like customer_zip must match the
# same text exactly, which keyword search does better than search by meaning.
#
# Everything read here is untrusted text (a code comment can carry a hidden instruction), and only files inside
# the repository folder are ever read.
import ast
import fnmatch
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

SKIP_DIRS = {".git", "__pycache__"}                       # never index these
CODE_SUFFIXES = {".py", ".sql", ".conf", ".cfg", ".yaml", ".yml"}

# ---------------------------------------------------------------------------
# Owners (CODEOWNERS): who a ticket for a path goes to
# ---------------------------------------------------------------------------

# Each non-comment line is "pattern owner". Returns a list of (pattern, owner) in file order.
def read_codeowners(repo: Path) -> list:
    path = repo / "CODEOWNERS"
    rules = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pattern, owner = line.split(None, 1)
                rules.append((pattern, owner.strip()))
    return rules

# The LAST matching rule wins, as on GitHub. A pattern ending in "/" matches everything under that folder;
# otherwise it is a wildcard match on the path (a leading "/" means "from the repository root").
def owner_of(path: str, rules: list):
    owner = None
    for pattern, who in rules:
        p = pattern.lstrip("/")
        if (p.endswith("/") and path.startswith(p)) or fnmatch.fnmatch(path, p):
            owner = who
    return owner

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk(path, start, end, kind, lines):
    # start and end are 1-based line numbers, both included
    return {"path": path, "start": start, "end": end, "kind": kind, "text": "\n".join(lines[start - 1:end])}

# A long Python file: one chunk per top-level function or class (including its decorators), and one chunk for
# each run of other top-level code in between (imports, constants, module-level code).
def split_python(path, lines):
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return [_chunk(path, 1, len(lines), "python", lines)]        # cannot parse: keep it whole
    chunks, covered = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            chunks.append(_chunk(path, start, node.end_lineno, f"python {type(node).__name__}", lines))
            covered.update(range(start, node.end_lineno + 1))
    run = []                                                         # the other lines, in runs
    for n in range(1, len(lines) + 2):
        if n <= len(lines) and n not in covered:
            run.append(n)
        elif run:
            if any(lines[i - 1].strip() for i in run):
                chunks.append(_chunk(path, run[0], run[-1], "python module code", lines))
            run = []
    return sorted(chunks, key=lambda c: c["start"])

# A long SQL file: one chunk per statement. A statement ends at a line that ends with ";", and the comment
# lines just above it belong to it.
def split_sql(path, lines):
    chunks, start = [], None
    for n, line in enumerate(lines, start=1):
        if start is None and line.strip():
            start = n
        if start is not None and re.sub(r"--.*$", "", line).rstrip().endswith(";"):
            chunks.append(_chunk(path, start, n, "sql statement", lines))
            start = None
    if start is not None:                                            # a last statement without a ";"
        chunks.append(_chunk(path, start, len(lines), "sql statement", lines))
    return chunks

def _kind(path: str) -> str:
    return {".py": "python", ".sql": "sql"}.get(Path(path).suffix, "config")

# All chunks of the repository. Files of max_lines or fewer lines stay whole.
def build_chunks(repo: Path, max_lines: int) -> list:
    repo = Path(repo).resolve()
    chunks = []
    for file in sorted(repo.rglob("*")):
        rel_parts = file.relative_to(repo).parts
        if not file.is_file() or any(part in SKIP_DIRS for part in rel_parts):
            continue
        # CODEOWNERS is read for owners (read_codeowners) but is not indexed: it mentions every file name and
        # would show up as a hit for almost any search.
        if file.suffix not in CODE_SUFFIXES:
            continue
        path = "/".join(rel_parts)                                   # always forward slashes, never absolute
        lines = file.read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        if len(lines) <= max_lines:
            chunks.append(_chunk(path, 1, len(lines), _kind(path), lines))
        elif file.suffix == ".py":
            chunks.extend(split_python(path, lines))
        elif file.suffix == ".sql":
            chunks.extend(split_sql(path, lines))
        else:
            chunks.append(_chunk(path, 1, len(lines), _kind(path), lines))
    return chunks

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

STOP = {"the", "and", "for", "with", "from", "this", "that", "into", "when", "what", "where", "which",
        "error", "failed", "failure", "task", "does", "not", "exist"}

# Words in code or in a query. A name like customer_zip is kept whole AND split into its parts, so a search for
# "zip" can still find it. Dotted names (orders.customer_zip) are split too.
def tokenize(text: str) -> list:
    tokens = []
    for word in re.findall(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*", text.lower()):
        tokens.append(word)
        parts = re.split(r"[._]", word)
        if len(parts) > 1:
            tokens.extend(p for p in parts if p)
    return tokens

# The identifiers in a query: words with an underscore or a dot (customer_zip, orders_load.sql).
def identifiers_in(query: str) -> list:
    return [w for w in re.findall(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", query) if "_" in w or "." in w]

class CodeIndex:
    def __init__(self, repo, max_lines: int = 80):
        self.repo = Path(repo).resolve()
        self.rules = read_codeowners(self.repo)
        self.chunks = build_chunks(self.repo, max_lines)
        # The path is part of what is searched, because a file name (orders_load) says a lot
        self.bm25 = BM25Okapi([tokenize(c["path"] + " " + c["text"]) for c in self.chunks])

    # The best chunks for a query, best first. Chunks that contain more of the query's identifiers verbatim come
    # first; BM25 orders the rest. Each hit also lists the lines that matched, so a ticket can name a line.
    def search(self, query: str, top_k: int = 5, path_prefix: str = "") -> list:
        identifiers = [i.lower() for i in identifiers_in(query)]
        # The plain words of the query as written (not the parts of identifiers), without very common words
        plain = [w.strip(".,:;()\"'").lower() for w in query.split()]
        words = [w for w in plain if len(w) >= 4 and w not in STOP and "_" not in w and "." not in w]
        scores = self.bm25.get_scores(tokenize(query))
        hits = []
        for chunk, bm25 in zip(self.chunks, scores):
            if path_prefix and not chunk["path"].startswith(path_prefix):
                continue
            haystack = (chunk["path"] + "\n" + chunk["text"]).lower()
            exact = sum(1 for i in identifiers if i in haystack)
            if exact == 0 and bm25 <= 0:
                continue
            # Every line that contains an identifier or a plain word of the query, in file order (at most 20).
            # The tool decides how many of them to show.
            needles = list(dict.fromkeys(identifiers + words))
            match_lines = [(chunk["start"] + k, line) for k, line in enumerate(chunk["text"].splitlines())
                           if any(n in line.lower() for n in needles)][:20]
            # A hit with no exact identifier and no matching line is only a keyword-score accident: drop it
            if exact == 0 and not match_lines:
                continue
            hits.append({**chunk, "owner": owner_of(chunk["path"], self.rules), "bm25": float(bm25),
                         "exact": exact, "match_lines": match_lines})
        hits.sort(key=lambda h: (h["exact"], h["bm25"]), reverse=True)
        return hits[:top_k]
