"""Fail-closed verification for scope-bound protected-change approvals."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

try:
    from .issue_payload import fetch_issue, task_fingerprint
except ImportError:
    from issue_payload import fetch_issue, task_fingerprint

APPROVAL_PREFIX = "Protected-change approval recorded by AltCards Orchestrator."
REVOCATION_PREFIX = "Protected-change approval revoked by AltCards Orchestrator."
APPROVAL_RE = re.compile(
    r"<!-- altcards-approval:v1 task=([0-9a-f]{64}) scope=([0-9a-f]{64}) approval=([0-9a-f]{64}) -->"
)


def normalize_scope(scope: str) -> str:
    return str(scope or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def scope_fingerprint(scope: str) -> str:
    return hashlib.sha256(normalize_scope(scope).encode("utf-8")).hexdigest()


def approval_fingerprint(task_sha256: str, scope_sha256: str) -> str:
    payload = f"altcards-sensitive-approval:v1\n{task_sha256}\n{scope_sha256}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_approval_comment(task_sha256: str, scope: str, confirmation: str) -> str:
    scope = normalize_scope(scope)
    scope_sha = scope_fingerprint(scope)
    approval_sha = approval_fingerprint(task_sha256, scope_sha)
    return (
        APPROVAL_PREFIX
        + "\n\nApproved scope (JSON):\n"
        + json.dumps(scope, ensure_ascii=False)
        + "\nUser confirmation (JSON):\n"
        + json.dumps(str(confirmation or "").strip(), ensure_ascii=False)
        + f"\n\n<!-- altcards-approval:v1 task={task_sha256} scope={scope_sha} approval={approval_sha} -->"
    )


def parse_approval_comment(body: str) -> dict | None:
    if not isinstance(body, str) or not body.startswith(APPROVAL_PREFIX):
        return None
    match = APPROVAL_RE.search(body)
    if not match:
        return None
    scope_marker = "Approved scope (JSON):\n"
    confirmation_marker = "\nUser confirmation (JSON):\n"
    start = body.find(scope_marker)
    if start < 0:
        return None
    start += len(scope_marker)
    end = body.find(confirmation_marker, start)
    if end < 0:
        return None
    try:
        scope = json.loads(body[start:end])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(scope, str):
        return None
    task_sha, scope_sha, approval_sha = match.groups()
    if scope_fingerprint(scope) != scope_sha:
        return None
    if approval_fingerprint(task_sha, scope_sha) != approval_sha:
        return None
    return {
        "task_sha256": task_sha,
        "scope_sha256": scope_sha,
        "approval_sha256": approval_sha,
        "approved_scope": scope,
    }


def _comment_author(comment: dict) -> str:
    author = comment.get("author") or comment.get("user") or {}
    if not isinstance(author, dict):
        return ""
    return str(author.get("login") or "").strip().casefold()


def _comment_time(comment: dict) -> str:
    return str(comment.get("createdAt") or comment.get("created_at") or "")


def latest_owner_decision(comments: list, expected_owner: str) -> dict | None:
    owner = str(expected_owner or "").strip().casefold()
    events = []
    for index, comment in enumerate(comments or []):
        if not isinstance(comment, dict) or _comment_author(comment) != owner:
            continue
        body = str(comment.get("body") or "")
        if body.startswith(APPROVAL_PREFIX):
            events.append((_comment_time(comment), index, "approved", body))
        elif body.startswith(REVOCATION_PREFIX):
            events.append((_comment_time(comment), index, "revoked", body))
    if not events:
        return None
    _, _, action, body = max(events, key=lambda x: (x[0], x[1]))
    return {"action": action, "body": body}


def evaluate_approval(
    issue: dict, expected_owner: str, expected_task_sha: str | None = None,
    require_approval: bool = False, expected_approval_sha: str | None = None,
) -> dict:
    labels = [str(x.get("name") or "") for x in issue.get("labels", []) if isinstance(x, dict)]
    label_present = "ai-sensitive-approved" in labels
    current_task_sha = task_fingerprint(issue)
    if expected_task_sha and current_task_sha != expected_task_sha:
        return {
            "valid": False,
            "allow_sensitive": False,
            "label_present": label_present,
            "status": "task_changed",
            "reason": "Issue task changed after the workflow captured its canonical TASK.",
            "task_sha256": current_task_sha,
        }
    if not label_present:
        if require_approval or expected_approval_sha:
            return {
                "valid": False,
                "allow_sensitive": False,
                "label_present": False,
                "status": "approval_removed",
                "reason": "A previously verified approval was removed before the workflow completed.",
                "task_sha256": current_task_sha,
            }
        return {
            "valid": True,
            "allow_sensitive": False,
            "label_present": False,
            "status": "not_required",
            "reason": "No protected-change approval label is present.",
            "task_sha256": current_task_sha,
        }

    decision = latest_owner_decision(issue.get("comments") or [], expected_owner)
    if not decision:
        reason = "Approval label exists, but no owner-authored fingerprinted approval comment was found."
        status = "missing_owner_approval"
    elif decision["action"] == "revoked":
        reason = "The latest owner approval decision is a revocation."
        status = "revoked"
    else:
        record = parse_approval_comment(decision["body"])
        if not record:
            reason = "The latest owner approval comment has invalid or missing fingerprint metadata."
            status = "invalid_record"
        elif record["task_sha256"] != current_task_sha:
            reason = "The approved Issue title, body, or user comments have changed since approval."
            status = "task_fingerprint_mismatch"
        elif expected_approval_sha and record["approval_sha256"] != expected_approval_sha:
            reason = "The owner approval or approved scope changed while the workflow was running."
            status = "approval_changed"
        else:
            return {
                "valid": True,
                "allow_sensitive": True,
                "label_present": True,
                "status": "verified",
                "reason": "Owner approval matches the current canonical Issue task and approved scope.",
                "task_sha256": current_task_sha,
                "scope_sha256": record["scope_sha256"],
                "approval_sha256": record["approval_sha256"],
            }
    return {
        "valid": False,
        "allow_sensitive": False,
        "label_present": True,
        "status": status,
        "reason": reason,
        "task_sha256": current_task_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-number", required=True, type=int)
    parser.add_argument("--expected-owner", required=True)
    parser.add_argument("--expected-task-sha")
    parser.add_argument("--expected-approval-sha")
    parser.add_argument("--require-approval", action="store_true")
    args = parser.parse_args()
    if args.issue_number < 1:
        parser.error("--issue-number must be positive")
    issue = fetch_issue(args.issue_number)
    result = evaluate_approval(
        issue, args.expected_owner, args.expected_task_sha,
        require_approval=args.require_approval,
        expected_approval_sha=args.expected_approval_sha,
    )
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"allow_sensitive={str(result['allow_sensitive']).lower()}\n")
            output.write(f"approval_status={result['status']}\n")
            output.write(f"verified_task_sha256={result['task_sha256']}\n")
            output.write(f"verified_approval_sha256={result.get('approval_sha256', '')}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: approval verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
