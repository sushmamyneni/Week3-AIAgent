# Step 2a: build the synthetic "enterprise" sandbox from the seed in sandbox_src.
#
# What it does:
#   1. deletes any old sandbox\ folder and starts clean
#   2. runs `git init` in sandbox\pipelines-repo
#   3. for each folder in sandbox_src\commits (01_..., 02_..., in order): copies its files into the
#      repository and makes ONE git commit, using the message, author and date from its COMMIT.json
#   4. copies the schema store (warehouse\schema_history.json) next to the repository
#
# The result is identical every run: same files, same commits, same dates, same commit ids. That is
# what lets the incident answer keys point at real lines and real history.
#
# Usage (from the Week3-AIAgent folder, Week 3 environment active):
#   python 2a_make_sandbox.py
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "sandbox_src"
SANDBOX = HERE / "sandbox"                 # ignored by git (see .gitignore); this script recreates it
REPO = SANDBOX / "pipelines-repo"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Run a git command inside the repository and return its output. check=True stops the script with an
# error if git fails, so a half-built sandbox is never left unnoticed.
#   -c commit.gpgsign=false   commits in this throwaway repo must not ask for a signing key
#   -c core.autocrlf=false    keep the files byte-for-byte as written (no CRLF conversion on Windows)
def git(*args, env=None):
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
        cwd=REPO, check=True, capture_output=True, text=True, env=env,
    )
    return result.stdout.strip()

# Windows marks files inside .git as read-only, which makes shutil.rmtree fail. This handler makes the
# file writable and tries again.
def make_writable_and_retry(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)

# Empty a folder but keep the folder itself. Windows will not delete a folder that another program is
# using (a terminal whose current directory is inside it, or an editor watching it), but it does allow
# deleting everything INSIDE it, which is all we need.
def empty_folder(folder: Path):
    for child in folder.iterdir():
        if child.is_dir():
            shutil.rmtree(child, onexc=make_writable_and_retry)
        else:
            child.chmod(stat.S_IWRITE)
            child.unlink()

# "Name <email>" -> ("Name", "email")
def split_author(author: str):
    name, email = author.rsplit("<", 1)
    return name.strip(), email.strip(" >")

# ---------------------------------------------------------------------------
# Step 1: start clean
# ---------------------------------------------------------------------------
# Safety check: only ever touch a folder that is really named "sandbox" inside this project
assert SANDBOX.name == "sandbox" and SANDBOX.parent == HERE, "refusing to clear an unexpected folder"
REPO.mkdir(parents=True, exist_ok=True)   # keep the folder if it exists
empty_folder(REPO)                         # remove only what is inside it (the old .git and files)


# ---------------------------------------------------------------------------
# Step 2: initialize the repository
# ---------------------------------------------------------------------------
# -b main names the first branch "main" (needs git 2.28 or newer)
git("init", "-b", "main")

# ---------------------------------------------------------------------------
# Step 3: one commit per seed folder, in order
# ---------------------------------------------------------------------------
# sorted() puts 01_..., 02_..., 03_... in order, which is the order of the history
for commit_dir in sorted((SEED / "commits").iterdir()):
    info = json.loads((commit_dir / "COMMIT.json").read_text(encoding="utf-8"))
    name, email = split_author(info["author"])

    # Copy this commit's files into the repository. dirs_exist_ok lets a later commit add files to a
    # folder that already exists, and a file with the same path REPLACES the older version.
    # COMMIT.json is metadata, not part of the repository, so it is skipped.
    shutil.copytree(commit_dir, REPO, dirs_exist_ok=True, ignore=shutil.ignore_patterns("COMMIT.json"))

    # Fix the author, the committer and both dates, so the commit id comes out the same every time
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": info["date"],
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": info["date"],
    }
    git("add", "-A")
    git("commit", "-m", info["message"], env=env)
    print(f"committed {commit_dir.name}: {info['message']}")

# ---------------------------------------------------------------------------
# Step 4: the schema store (not part of the git repository)
# ---------------------------------------------------------------------------
(SANDBOX / "warehouse").mkdir(exist_ok=True)

shutil.copy2(SEED / "warehouse" / "schema_history.json", SANDBOX / "warehouse" / "schema_history.json")

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print()
print(git("log", "--format=%h  %ad  %an  %s", "--date=short"))
print("files in the repository:", len(git("ls-files").splitlines()))
print("head commit id:", git("rev-parse", "HEAD"))
