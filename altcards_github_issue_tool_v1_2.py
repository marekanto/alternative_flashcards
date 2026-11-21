"""
title: AltCards GitHub Issue Creator
author: OpenAI
description: Searches and creates AltCards issues, and records owner-approved protected-change scopes.
required_open_webui_version: 0.6.0
requirements: pydantic
version: 1.2.0
"""

from pydantic import BaseModel, Field
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from difflib import SequenceMatcher
import json
import re


MAX_ISSUE_TITLE_CHARS = 120
MAX_ISSUE_BODY_CHARS = 3000
MAX_ISSUE_TITLE_BODY_CHARS = 3200


class Tools:
    class Valves(BaseModel):
        GITHUB_TOKEN: str = Field(default="", description="Fine-grained GitHub token with Issues: Read and write for the selected repository only.")
        GITHUB_OWNER: str = Field(default="marekanto", description="GitHub repository owner.")
        GITHUB_REPO: str = Field(default="alternative_flashcards", description="GitHub repository name.")
        GITHUB_API_URL: str = Field(default="https://api.github.com", description="GitHub API base URL.")

    def __init__(self):
        self.valves = self.Valves()

    def _request(self, method: str, path: str, payload: Optional[dict] = None):
        if not self.valves.GITHUB_TOKEN:
            raise RuntimeError("Missing GITHUB_TOKEN. Configure it in the tool's Valves settings.")

        url = self.valves.GITHUB_API_URL.rstrip("/") + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", "Bearer " + self.valves.GITHUB_TOKEN)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "AltCards-OpenWebUI-IssueCreator")
        if data is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req, timeout=25) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach GitHub API: {exc}") from exc

    def _repo_path(self) -> str:
        owner = self.valves.GITHUB_OWNER.strip()
        repo = self.valves.GITHUB_REPO.strip()
        if not owner or not repo or "/" in owner or "/" in repo:
            raise RuntimeError("Invalid GitHub owner or repository configuration.")
        return f"/repos/{owner}/{repo}"

    def _list_open_issues(self, max_pages: int = 3):
        items = []
        for page in range(1, max_pages + 1):
            batch = self._request(
                "GET",
                f"{self._repo_path()}/issues?state=open&per_page=100&page={page}",
            )
            if not isinstance(batch, list) or not batch:
                break
            items.extend(x for x in batch if "pull_request" not in x)
            if len(batch) < 100:
                break
        return items

    def _labels(self, issue: dict):
        return [x.get("name", "") for x in issue.get("labels", [])]

    def _normalise(self, value: str) -> str:
        value = value.casefold()
        value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
        return " ".join(value.split())

    def _title_duplicate(self, title: str, issues: list):
        target = self._normalise(title)
        for issue in issues:
            existing = self._normalise(issue.get("title", ""))
            if not existing:
                continue
            ratio = SequenceMatcher(None, target, existing).ratio()
            if target == existing or (len(target) >= 28 and len(existing) >= 28 and ratio >= 0.93):
                return issue
        return None

    def search_open_issues(self, query: str, limit: int = 20) -> str:
        """Search open repository issues before proposing a new task.
        :param query: Short description or title of the proposed task.
        :param limit: Maximum number of candidate issues to return (1-30).
        """
        if not query or len(query.strip()) < 3:
            return "Provide a more specific search query (at least 3 characters)."
        limit = max(1, min(int(limit), 30))
        issues = self._list_open_issues()
        q = self._normalise(query)
        terms = {x for x in q.split() if len(x) > 2}
        ranked = []
        for issue in issues:
            title = issue.get("title", "")
            body = issue.get("body") or ""
            haystack = self._normalise(title + " " + body[:3000])
            hay_terms = set(haystack.split())
            overlap = len(terms & hay_terms) / max(1, len(terms))
            title_ratio = SequenceMatcher(None, q, self._normalise(title)).ratio()
            score = max(overlap, title_ratio)
            if score >= 0.18:
                ranked.append((score, issue))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        if not ranked:
            return "No similar open issues found in the checked repository issues. This is not proof that no duplicate exists; compare the proposed task with the project context before creating it."

        results = []
        for score, issue in ranked[:limit]:
            results.append({
                "number": issue["number"],
                "title": issue.get("title", ""),
                "url": issue.get("html_url", ""),
                "labels": self._labels(issue),
                "similarity_hint": round(score, 2),
                "body_excerpt": (issue.get("body") or "")[:500],
            })
        return json.dumps({"open_issue_candidates": results, "instruction": "Compare these candidates semantically. Do not create a duplicate. If one is the same task, explain that and continue the existing issue instead."}, ensure_ascii=False, indent=2)

    def _get_issue(self, issue_number: int):
        number = int(issue_number)
        if number < 1:
            raise ValueError("Issue number must be a positive integer.")
        issue = self._request("GET", f"{self._repo_path()}/issues/{number}")
        if "pull_request" in issue:
            raise RuntimeError(f"#{number} is a pull request, not an Issue.")
        if issue.get("state") != "open":
            raise RuntimeError(f"Issue #{number} is not open; protected approval was not changed.")
        return issue

    def _ensure_label_exists(self, name: str):
        labels = self._request("GET", f"{self._repo_path()}/labels?per_page=100")
        if any(item.get("name", "").casefold() == name.casefold() for item in labels if isinstance(item, dict)):
            return
        self._request("POST", f"{self._repo_path()}/labels", {
            "name": name,
            "color": "BFD4F2",
            "description": "Owner-approved scope for protected AltCards changes",
        })

    def approve_sensitive_issue(self, issue_number: int, approved_scope: str, user_confirmation: str) -> str:
        """Apply ai-sensitive-approved to one open Issue after explicit owner approval in the current chat.

        IMPORTANT: Call ONLY after the user has clearly approved the exact scope in the immediately preceding conversation turn. A generic acknowledgement is not enough unless it directly answers the approval question. This tool records the approved scope in an Issue comment for auditability.
        :param issue_number: Existing open GitHub Issue number.
        :param approved_scope: Concise exact scope the user approved, including protected subsystem and intended change.
        :param user_confirmation: The user's actual affirmative message (for example, 'ok' or 'tak'), quoted verbatim or faithfully transcribed.
        """
        try:
            number = int(issue_number)
        except (TypeError, ValueError):
            return "Approval not applied: issue_number must be a positive integer."
        scope = (approved_scope or "").strip()
        confirmation = (user_confirmation or "").strip()
        if number < 1:
            return "Approval not applied: issue_number must be a positive integer."
        if len(scope) < 15 or len(scope) > 1500:
            return "Approval not applied: approved_scope must contain 15-1500 characters and identify the exact protected change."
        if not confirmation or len(confirmation) > 300:
            return "Approval not applied: the user's affirmative confirmation text is required."

        issue = self._get_issue(number)
        self._ensure_label_exists("ai-sensitive-approved")
        current_labels = self._labels(issue)
        if "ai-sensitive-approved" in current_labels:
            return json.dumps({
                "approved": True,
                "already_present": True,
                "number": number,
                "url": issue.get("html_url", ""),
                "approved_scope": scope,
                "message": "The approval label was already present. Verify that its existing approval comment matches this scope before proceeding.",
            }, ensure_ascii=False, indent=2)

        # Add the label only after the caller has passed the explicit user confirmation.
        self._request("POST", f"{self._repo_path()}/issues/{number}/labels", {
            "labels": ["ai-sensitive-approved"]
        })
        try:
            audit = (
                "Protected-change approval recorded by AltCards Orchestrator.\n\n"
                f"Approved scope: {scope}\n\n"
                f"User confirmation in ChatGPT: {confirmation}\n\n"
                "This approval applies only to the scope above for this Issue. Any material scope change requires renewed approval."
            )
            self._request("POST", f"{self._repo_path()}/issues/{number}/comments", {"body": audit})
        except Exception:
            # Fail closed: if the audit comment cannot be recorded, remove the label again.
            try:
                from urllib.parse import quote
                self._request("DELETE", f"{self._repo_path()}/issues/{number}/labels/{quote('ai-sensitive-approved', safe='')}")
            finally:
                raise

        return json.dumps({
            "approved": True,
            "already_present": False,
            "number": number,
            "url": issue.get("html_url", ""),
            "label": "ai-sensitive-approved",
            "approved_scope": scope,
            "audit_comment_recorded": True,
            "message": "Protected-change approval label added to this Issue and the approved scope recorded in an audit comment.",
        }, ensure_ascii=False, indent=2)

    def revoke_sensitive_approval(self, issue_number: int, reason: str) -> str:
        """Remove ai-sensitive-approved from one open Issue when its approved scope is no longer valid.
        Call when the task scope materially changes or the user withdraws approval.
        :param issue_number: Existing open GitHub Issue number.
        :param reason: Concise reason for revoking the prior approval.
        """
        try:
            number = int(issue_number)
        except (TypeError, ValueError):
            return "Approval not revoked: issue_number must be a positive integer."
        reason = (reason or "").strip()
        if number < 1 or len(reason) < 8 or len(reason) > 1000:
            return "Approval not revoked: provide a positive Issue number and a reason of 8-1000 characters."
        issue = self._get_issue(number)
        if "ai-sensitive-approved" not in self._labels(issue):
            return json.dumps({"revoked": True, "already_absent": True, "number": number}, ensure_ascii=False)
        from urllib.parse import quote
        self._request("DELETE", f"{self._repo_path()}/issues/{number}/labels/{quote('ai-sensitive-approved', safe='')}")
        self._request("POST", f"{self._repo_path()}/issues/{number}/comments", {
            "body": "Protected-change approval revoked by AltCards Orchestrator.\n\nReason: " + reason
        })
        return json.dumps({
            "revoked": True,
            "number": number,
            "url": issue.get("html_url", ""),
            "label": "ai-sensitive-approved",
            "message": "Approval label removed and revocation recorded.",
        }, ensure_ascii=False, indent=2)

    def create_github_issue(self, title: str, body: str) -> str:
        """Create a new AltCards issue and place it into the serial AI queue.
        Call only after requirements are clear, acceptance criteria are present, and search_open_issues has been used.
        :param title: Concise, actionable issue title.
        :param body: Complete issue description with context, scope, and testable acceptance criteria.
        """
        title = (title or "").strip()
        body = (body or "").strip()
        if not title or len(title) > MAX_ISSUE_TITLE_CHARS:
            return f"Issue not created: title must contain 1-{MAX_ISSUE_TITLE_CHARS} characters (received {len(title)})."
        if len(body) < 80:
            return "Issue not created: description is too short. Include context, scope, and testable acceptance criteria."
        if len(body) > MAX_ISSUE_BODY_CHARS:
            return (
                f"Issue not created: body has {len(body)} characters; hard limit is "
                f"{MAX_ISSUE_BODY_CHARS}. Ask ask_issue_writer to compress the Issue, "
                "remove full FIND/REPLACE source dumps, preserve acceptance criteria, and retry."
            )
        if len(title) + len(body) > MAX_ISSUE_TITLE_BODY_CHARS:
            return (
                f"Issue not created: title + body has {len(title) + len(body)} characters; "
                f"limit is {MAX_ISSUE_TITLE_BODY_CHARS}. Shorten the title or body."
            )

        open_issues = self._list_open_issues()
        duplicate = self._title_duplicate(title, open_issues)
        if duplicate:
            return json.dumps({
                "created": False,
                "reason": "Possible duplicate title",
                "existing_issue": {
                    "number": duplicate["number"],
                    "title": duplicate.get("title", ""),
                    "url": duplicate.get("html_url", ""),
                    "labels": self._labels(duplicate),
                },
                "next_step": "Do not create another issue. Tell the user about the existing issue and determine whether it should be continued instead.",
            }, ensure_ascii=False, indent=2)

        # Create without labels first. Then add exactly one queue label so that
        # the 'labeled' webhook starts the current workflow for the first task.
        issue = self._request("POST", f"{self._repo_path()}/issues", {
            "title": title,
            "body": body,
        })

        active = [
            item for item in open_issues
            if "ai-task" in self._labels(item)
        ]
        queue_label = "ai-queue" if active else "ai-task"
        self._request("POST", f"{self._repo_path()}/issues/{issue['number']}/labels", {
            "labels": [queue_label]
        })

        return json.dumps({
            "created": True,
            "number": issue["number"],
            "title": issue.get("title", title),
            "url": issue.get("html_url", ""),
            "queue_status": "queued" if active else "started",
            "assigned_label": queue_label,
            "active_issue_detected": active[0]["number"] if active else None,
            "message": "Issue created and added to the AltCards serial AI queue. Do not create another issue for the same request.",
        }, ensure_ascii=False, indent=2)
