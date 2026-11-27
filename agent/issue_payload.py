"""Canonical GitHub Issue payload shared by the Clarifier and coding workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

BOT_LOGIN = "github-actions[bot]"
AUTOMATION_PREFIXES = (
    "Protected-change approval recorded by AltCards Orchestrator.",
    "Protected-change approval revoked by AltCards Orchestrator.",
    "❌ **Agent nie ukończył zadania. Kolejka została zatrzymana.**",
    "🤔 **Potrzebuję doprecyzowania**",
    "✅ **Gotowe**",
)


def _author_login(comment: dict) -> str:
    author = comment.get("author") or comment.get("user") or {}
    if not isinstance(author, dict):
        return ""
    return str(author.get("login") or "").strip().casefold()


def comment_body(comment: dict) -> str:
    return str(comment.get("body") or "")


def is_automation_comment(comment: dict) -> bool:
    if _author_login(comment) == BOT_LOGIN.casefold():
        return True
    body = comment_body(comment)
    return any(body.startswith(prefix) for prefix in AUTOMATION_PREFIXES)


def filter_user_comments(comments) -> list[str]:
    """Return user-authored comment bodies using the workflow's canonical filters."""
    result = []
    for comment in comments or []:
        if not isinstance(comment, dict) or is_automation_comment(comment):
            continue
        body = comment_body(comment).strip()
        if body:
            result.append(body)
    return result


def compose_task(issue: dict) -> str:
    """Compose exactly the same TASK payload used for model calls and approval hashes."""
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    comments = filter_user_comments(issue.get("comments"))
    return title + "\n\n" + body + "\n\n" + "\n\n---\n\n".join(comments)


def task_fingerprint(issue: dict) -> str:
    return hashlib.sha256(compose_task(issue).encode("utf-8")).hexdigest()


def fetch_issue(issue_number: int) -> dict:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "title,body,comments,labels"],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise RuntimeError("GitHub CLI returned an unexpected Issue payload")
    return data


def write_github_output(values: dict) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.issue_number < 1:
        parser.error("--issue-number must be positive")
    issue = fetch_issue(args.issue_number)
    task = compose_task(issue)
    if not task.strip() or len(task) > 4000:
        raise RuntimeError(f"Canonical TASK must contain 1-4000 characters; got {len(task)}")
    args.output.write_text(task, encoding="utf-8")
    digest = hashlib.sha256(task.encode("utf-8")).hexdigest()
    write_github_output({"task_sha256": digest, "task_chars": len(task)})
    print(json.dumps({"task_sha256": digest, "task_chars": len(task)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
