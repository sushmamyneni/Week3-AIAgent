# The ticket contract: the structured record the triage agent hands over, and the escalation report for a
# failure it could not diagnose.
#
# A ticket is the interface between this agent and, later, a second agent that makes the change. A second
# agent should read fields, not interpret prose, so every field has a fixed shape and a ticket is VALIDATED
# against these models before anything acts on it. A ticket that does not fit is rejected, not repaired.
#
# This file holds the models (step 5a). The tools that write tickets are added to it in step 6.
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# extra="forbid" rejects any field that is not declared. A made-up field (or text smuggled in under a new
# name) cannot ride along in a ticket.
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

# Rules every ticket carries, whatever the model wrote. The validator below adds any that are missing.
MANDATORY_CONSTRAINTS = ["never touch the main branch", "never act on a real system"]

# ---------------------------------------------------------------------------
# The parts of a ticket
# ---------------------------------------------------------------------------

# One fact the diagnosis rests on. ref is the evidence number from the run (E3 is the third tool call, L is
# the log), quote is the exact text, and citation is where a person can find it: "sql/orders_load.sql:6",
# a commit id, a migration, or "docs path > heading". The citation is derived by code, never by the model.
class Evidence(Strict):
    kind: Literal["log", "doc", "code", "schema", "commit", "incident"]
    ref: str = Field(min_length=1, max_length=10)
    quote: str = Field(min_length=1, max_length=400)
    citation: str = Field(min_length=1, max_length=300)

# Where the change goes: a file inside the repository, the line, and the setting or identifier changed
class Target(Strict):
    file: str = Field(min_length=1, max_length=200)
    line: Optional[int] = Field(default=None, ge=1)
    key: str = Field(default="", max_length=200)

    # The same rule as the tools use for paths: relative, inside the repository, nothing clever
    @field_validator("file")
    @classmethod
    def file_inside_repository(cls, v: str) -> str:
        if ".." in v or v.startswith(("/", "\\")) or ":" in v:
            raise ValueError("file must be a relative path inside the repository")
        return v

# What to do. A retry needs no target. A code or configuration change must say exactly where and what.
class ProposedAction(Strict):
    type: Literal["retry", "code_change", "config_change"]
    target: Optional[Target] = None
    old_value: str = Field(default="", max_length=500)
    new_value: str = Field(default="", max_length=500)
    bounds: str = Field(default="", max_length=300)       # the allowed range, such as "2Gi to 8Gi"

    @model_validator(mode="after")
    def change_is_specific(self):
        if self.type in ("code_change", "config_change"):
            if self.target is None or self.target.line is None:
                raise ValueError(f"a {self.type} needs a target with a file and a line")
            if not self.new_value.strip():
                raise ValueError(f"a {self.type} needs a new_value: the exact change")
        return self

# The checkable facts behind the choice of action. There is no "confidence: 0.9" the model reported.
class ConfidenceSignals(Strict):
    best_doc_score: Optional[float] = Field(default=None, ge=0, le=1)   # best docs match, None if docs were not used
    sources_agreeing: int = Field(ge=0, le=5)                           # independent source kinds that back the cause
    similar_past_incident: bool = False
    change_allowlisted: bool                                            # the kind of change is one a person has pre-approved
    validation: Literal["passed", "failed", "not_run"] = "not_run"

class Run(Strict):
    dag_or_app: str = Field(min_length=1)
    run_id: Optional[str] = None
    task: Optional[str] = None
    failed_at: str = Field(min_length=1)

# ---------------------------------------------------------------------------
# The ticket
# ---------------------------------------------------------------------------
class Ticket(Strict):
    ticket_id: str = Field(pattern=r"^T-[0-9a-f]{8}$")
    incident_id: str = Field(min_length=1)
    system: Literal["airflow", "spark"]
    run: Run
    root_cause: str = Field(min_length=20, max_length=800)
    evidence: list[Evidence] = Field(min_length=1, max_length=12)
    proposed_action: ProposedAction
    confidence_signals: ConfidenceSignals
    constraints: list[str] = Field(default_factory=list)
    owner: Optional[str] = Field(default=None, pattern=r"^@[\w.-]+/[\w.-]+$")      # a team from CODEOWNERS, such as @acme/orders-team
    status: Literal["draft", "approved", "fix_in_progress", "pr_opened", "merged", "rejected"] = "draft"
    approved_by: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @model_validator(mode="after")
    def whole_ticket_rules(self):
        # The mandatory rules are always present, in front, whatever the model wrote
        extra = [c for c in self.constraints if c not in MANDATORY_CONSTRAINTS]
        self.constraints = MANDATORY_CONSTRAINTS + extra
        # A change ticket must say who owns the code, so that it can be routed
        if self.proposed_action.type in ("code_change", "config_change") and not self.owner:
            raise ValueError("a code or configuration change needs an owner (from CODEOWNERS)")
        # Approval is a human's act: a draft has no approver, and an approved ticket must name one
        if self.status == "draft" and self.approved_by:
            raise ValueError("a draft ticket cannot have an approver")
        if self.status != "draft" and self.status != "rejected" and not self.approved_by:
            raise ValueError(f"a ticket with status {self.status!r} must name who approved it")
        return self

# One failure gets one ticket id, so asking twice for the same failure cannot create two tickets
def ticket_id_for(dedupe_key: str) -> str:
    return "T-" + hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:8]

# ---------------------------------------------------------------------------
# The escalation report: the hand-off when the agent cannot propose a safe, cited action
# ---------------------------------------------------------------------------
# Nothing is written. A person receives what the agent found, so they start from there, not from zero.
class Escalation(Strict):
    incident_id: str = Field(min_length=1)
    reason: str = Field(min_length=10, max_length=500)
    found: list[str] = Field(default_factory=list, max_length=12)          # what the evidence did show
    not_found: list[str] = Field(default_factory=list, max_length=12)      # what was looked for and is missing
    notes: list[str] = Field(default_factory=list, max_length=12)          # tool failures, retries, security notes
    owner: Optional[str] = Field(default=None, pattern=r"^@[\w.-]+/[\w.-]+$")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

# ---------------------------------------------------------------------------
# The write: create_ticket (step 6a)
# ---------------------------------------------------------------------------
# This is the only function in the project that writes a ticket, and it is only ever called from the step that
# runs AFTER a human approved. It does not decide anything: it checks the ticket, and writes it once.

# A fingerprint of a ticket's content. The approval screen shows it and the approval must send it back, so a
# ticket that changed after it was shown (or a stale approval of an older draft) is caught. sort_keys makes it
# independent of the order the fields happen to be in.
def ticket_hash(ticket: dict) -> str:
    text = json.dumps(ticket, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# Who approved: a short name or an email address. Nothing that could be a path or a long text.
APPROVER = re.compile(r"[\w .@-]{1,80}")

# Write an approved ticket to <folder>/<ticket_id>.json and return what happened. The ticket arrives as a
# draft (a dictionary); it is validated again here, with status "approved" and the approver's name, which the
# contract demands. The caller's dictionary is not changed.
#
# EXACTLY ONCE: the file is created in exclusive mode ("x"), which fails if the file already exists. The id
# comes from the failure (ticket_id_for), so a second approval, a resume after a crash, or a repeated event
# finds the first file and writes nothing. An existing ticket is never overwritten.
def create_ticket(ticket: dict, approved_by: str, folder) -> dict:
    approved_by = (approved_by or "").strip()
    if not APPROVER.fullmatch(approved_by):
        raise ValueError("approved_by must be a short name or an email address")
    approved = Ticket.model_validate({**ticket, "status": "approved", "approved_by": approved_by})
    text = approved.model_dump_json(indent=2)               # serialise first: a failure here leaves no file behind

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{approved.ticket_id}.json"            # the id matches T-xxxxxxxx, so it is always a safe file name
    try:
        with open(path, "x", encoding="utf-8") as f:
            try:
                f.write(text)
            except BaseException:                           # never leave half a ticket that would block the real one
                f.close()
                path.unlink(missing_ok=True)
                raise
    except FileExistsError:
        return {"created": False, "path": str(path), "reason": "a ticket for this failure already exists"}
    return {"created": True, "path": str(path), "reason": ""}

# Read a written ticket back, or None if there is none. The inbox and the tests use this.
def load_ticket(ticket_id: str, folder):
    if not re.fullmatch(r"T-[0-9a-f]{8}", ticket_id):
        return None
    path = Path(folder) / f"{ticket_id}.json"
    return Ticket.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None
