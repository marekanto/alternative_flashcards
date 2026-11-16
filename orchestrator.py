import os
import subprocess
import sys
from pathlib import Path

from code_agent import propose
from context_builder import build_context
from patch_validator import apply_operations, enforce_file_allowlist, enforce_sensitive_guard
from qa_agent import review
from settings import TARGET_FILE

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout


def main():
    task = os.getenv("TASK", "").strip()
    if not task or len(task) > 2500:
        raise RuntimeError("TASK must contain 1–2500 characters")
    allow_sensitive = os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"

    if run(["git", "status", "--porcelain"]).strip():
        raise RuntimeError("Checkout is not clean before agent run")

    source, context = build_context(task, ROOT)
    print(f"Context selected: {len(context)} chars; index.html: {len(source)} chars")
    proposal = propose(task, context)
    print(f"Code agent: {proposal.get('status')} — {proposal.get('summary', '')}")
    updated = apply_operations(source, proposal)

    enforce_sensitive_guard(source, updated, allow_sensitive)
    (ROOT / TARGET_FILE).write_text(updated, encoding="utf-8")

    changed = set(run(["git", "diff", "--name-only"]).splitlines())
    enforce_file_allowlist(changed)
    diff = run(["git", "diff", "--", TARGET_FILE])
    if not diff.strip():
        raise RuntimeError("No diff produced")

    # Independent bounded review call. A rejection blocks the PR.
    verdict = review(task, diff)
    print(f"QA verdict: {verdict.get('verdict')}")
    for finding in verdict.get("findings", [])[:8]:
        print("QA:", finding)
    if verdict.get("verdict") != "pass":
        raise RuntimeError("QA did not pass; no Pull Request will be created")

    print("AI patch passed static validation and independent review.")
    print("SUMMARY:", proposal.get("summary", "AltCards change"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
