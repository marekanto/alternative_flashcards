"""
tests/smoke_test.py — AltCards
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))

from agent.patch_validator import SensitiveChangeNotApproved, changed_protected_areas, enforce_sensitive_guard  # noqa: E402
from agent.context_builder import build_context, MAX_CONTEXT_CHARS, TARGET_FILE  # noqa: E402

SOURCE = (ROOT / TARGET_FILE).read_text(encoding="utf-8")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_generic_xp_task_is_complete() -> None:
    task = "Sprawdź dlaczego XP nie zwiększa się i czy może być gubione"
    res = build_context(task, SOURCE)
    ctx = res["context"]

    _assert(res["chars"] <= MAX_CONTEXT_CHARS,
            f"kontekst przekracza limit: {res['chars']} > {MAX_CONTEXT_CHARS}")
    _assert(not res["missing"], f"brakują bloki: {res['missing']}")
    _assert(not res["over_limit"], f"pominięto: {res['over_limit']}")

    required = [
        "function getLevelInfo", "function getRank", "function render",
        "function vStudy", "function vStats",
        "function ans", "function undoAnswer", "function grade",
        "async startSession", "async download", "async logXp",
        "queueUpload", "async upload", "async checkBonuses",
        "const defaults", "const save", "try{S=JSON.parse",
        "lvl-display", "S.settings.xp", "get_leaderboard", "xp_logs",
    ]
    for token in required:
        _assert(token in ctx, f"brak w kontekście XP: {token!r}")

    _assert(
        "return { level: lvl, currentXp: tempXp, nextLevelXp: req };" in ctx,
        "getLevelInfo ucięty — brak `return`",
    )
    _assert(
        "Sync?.queueUpload" in ctx,
        "save() ucięty — brak queueUpload",
    )


def test_ranking_bonus_task() -> None:
    task = "Dlaczego bonus XP za top 3 w rankingu dziennym nie jest widoczny w levelu?"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("checkBonuses", "get_leaderboard", "logXp",
                  "S.settings.xp", "serverXp", "xp_logs"):
        _assert(token in ctx, f"brak w kontekście bonusu: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


def test_login_task() -> None:
    task = "Usuń podtytuł na ekranie logowania i zmień tagline"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("function showLogin", "changeLoginLang",
                  "login.tagline", "login.subtitle"):
        _assert(token in ctx, f"brak w kontekście logowania: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


def test_brace_matcher_handles_templates_and_nested() -> None:
    from agent.context_builder import _match_braces
    sample = 'function f(){ const x = `a${ {b:1}.b }c`; if(1){return 2} }'
    start = sample.index("{")
    end = _match_braces(sample, start)
    _assert(end is not None, "matcher zwrócił None")
    _assert(sample[start:end].endswith("}"), "matcher nie domknął")


def test_sensitive_guard_requires_explicit_approval() -> None:
    before = "const defaults={newLimit:50,xp:0};"
    after = "const defaults={newLimit:50,dailyGoal:20,xp:0};"
    try:
        enforce_sensitive_guard(before, after, False)
    except SensitiveChangeNotApproved:
        pass
    else:
        raise AssertionError("protected defaults change passed without approval")

    enforce_sensitive_guard(before, after, True)

    # Appending HTML outside the script must not look like a saveSettings edit.
    closing_body = SOURCE.rfind("</body>")
    _assert(closing_body >= 0, "source HTML has no closing body tag")
    with_comment = (
        SOURCE[:closing_body]
        + "<!-- AltCards guard regression -->\n"
        + SOURCE[closing_body:]
    )
    changed = changed_protected_areas(SOURCE, with_comment)
    _assert(
        "saveSettings" not in changed,
        f"Trailing HTML comment falsely marked saveSettings as changed: {changed}",
    )


def test_validation_failure_refines_next_instruction() -> None:
    import orchestrator
    from unittest.mock import patch

    expected = "Narrowed instruction with exact correction"
    with patch.object(
        orchestrator,
        "refine_task",
        return_value={"ok": True, "refined_task": expected, "reasoning": "added validation feedback"},
    ) as mocked:
        actual = orchestrator._refine_after_failure(
            "original task", "previous instruction", "FIND text occurs 2 times",
            "(Patch rejected before a diff was created.)", 1,
        )
    _assert(actual == expected, "retry helper did not use the refined instruction")
    _assert(mocked.call_args.args[1] == [
        "Patch attempt 1 failed validation: FIND text occurs 2 times"
    ], "retry helper did not pass the validation error to the refiner")



def test_card_management_context_has_full_dependencies() -> None:
    task = """Przenieś akcje zarządzania taliami z Talie do Ustawień.

Zmiany w kodzie:
- Przerób dlist na wspólny renderer listy.
- Wyświetl listę w vCards i vSettings.
- Zachowaj obsługę shareDeck, unshareDeck i deckMenu.
- Zaktualizuj tut.g.decks i tut.g.settings.

Kryteria: po akcji save(); render(); obie listy są aktualne.

Nie zmieniaj
- SRS, XP, dzienny limit, undo, Sync, save/load, auth.
"""
    res = build_context(task, SOURCE)
    ctx = res["context"]
    _assert(res["ok"], f"kontekst talii niekompletny: missing={res['missing']} over={res['over_limit']}")
    for token in (
        "function vCards", "function vSettings", "function dlist",
        "function shareDeck", "function unshareDeck", "function deckMenu",
        "Sync.shareDeck(deck)", "Sync.unshareDeck(deck.sharedId)",
        "document.body.appendChild(m);", "'tut.g.decks':", "'tut.g.settings':",
    ):
        _assert(token in ctx, f"brak pełnej zależności w kontekście talii: {token!r}")
    _assert(not any(name in res["included"] for name in ("vStudy", "vStats", "ans", "undoAnswer", "grade")),
            "wykluczony XP/Undo nie powinien powodować dołączania kontekstu XP")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit kontekstu talii")


def test_agent_prompts_receive_complete_task() -> None:
    from unittest.mock import patch
    import code_agent
    import qa_agent

    task = "A" * 3900
    with patch.object(code_agent, "chat", return_value={"status": "need_context", "summary": "test"}) as code_chat:
        code_agent.propose(task, "source context")
        _assert(task in code_chat.call_args.args[1], "Code Agent task został obcięty")
    with patch.object(qa_agent, "chat", return_value={"verdict": "pass", "findings": []}) as qa_chat:
        qa_agent.review(task, "diff")
        _assert(task in qa_chat.call_args.args[1], "QA task został obcięty")

def test_context_builder_targeted_expansion() -> None:
    source = """
const outside = () => 1;
function shareDeck(id) {
  return Sync.shareDeck(id);
}
function unshareDeck(id) {
  return Sync.unshareDeck(id);
}
"""
    res = build_context("Change a small UI label", source, extra_names=["shareDeck", "unshareDeck"])
    _assert(res["ok"], f"targeted context should be complete: {res['missing']} {res['over_limit']}")
    _assert("expanded:shareDeck" in res["included"], "shareDeck expansion was not included")
    _assert("expanded:unshareDeck" in res["included"], "unshareDeck expansion was not included")
    _assert("return Sync.shareDeck(id);" in res["context"], "expanded function body is incomplete")


def test_need_context_symbol_extraction() -> None:
    import orchestrator
    proposal = {
        "status": "need_context",
        "reason": "Need complete `shareDeck` and function unshareDeck source.",
        "missing_context": ["deckMenu"],
    }
    names = orchestrator._requested_context_names(proposal, "Move shareDeck actions")
    _assert(set(names) >= {"shareDeck", "unshareDeck", "deckMenu"}, f"missing symbols not extracted: {names}")

    html_proposal = {
        "status": "need_context",
        "reason": "Need the exact source around the closing body tag.",
        "missing_context": ["closing </body> tag and adjacent literal HTML in index.html"],
    }
    html_names = orchestrator._requested_context_names(
        html_proposal, "Add a comment immediately before the closing </body> tag"
    )
    _assert(html_names == ["</body>"], f"HTML closing tag was not extracted cleanly: {html_names}")
    old_comment = "<!-- AltCards Repair Loop B validation 2026-11-23 -->"
    new_comment = "<!-- AltCards Repair Loop B validation complete 2026-11-23 -->"
    replace_task = f"Replace this HTML comment:\n{old_comment}\nwith:\n{new_comment}"
    comment_proposal = {
        "status": "need_context",
        "reason": "Target HTML comment is not present in supplied source excerpts",
        "missing_context": [],
    }
    comment_names = orchestrator._requested_context_names(comment_proposal, replace_task)
    _assert(old_comment in comment_names, f"Existing HTML comment was not extracted: {comment_names}")
    _assert(new_comment in comment_names, f"Replacement HTML comment was not extracted: {comment_names}")

    # Context expansion must retrieve the existing comment, while tolerating
    # the replacement comment not being present in source yet.
    comment_source = f"<html><body><main>Content</main>{old_comment}\n</body></html>"
    comment_context = build_context(
        "Replace an existing HTML comment",
        comment_source,
        extra_names=[old_comment, new_comment],
    )
    _assert(
        comment_context["ok"],
        f"Exact comment expansion should succeed: {comment_context['missing']} {comment_context['over_limit']}",
    )
    _assert(
        "expanded:html-comment" in comment_context["included"],
        "HTML comment context was not included",
    )
    _assert(
        old_comment in comment_context["context"],
        "Existing HTML comment is absent from expanded context",
    )


def test_orchestrator_recovers_need_context() -> None:
    import tempfile
    from unittest.mock import patch
    import orchestrator

    with tempfile.TemporaryDirectory(prefix="altcards-context-recovery-") as td:
        root = pathlib.Path(td)
        target = root / "index.html"
        target.write_text("baseline source", encoding="utf-8")
        initial_context = {"context": "BASE CONTEXT", "chars": 12, "included": ["base"],
                           "missing": [], "over_limit": [], "ok": True}
        expanded_context = {"context": "EXPANDED CONTEXT WITH renderCards", "chars": 34,
                            "included": ["base", "expanded:renderCards"],
                            "missing": [], "over_limit": [], "ok": True}
        proposals = [
            {"status": "need_context", "summary": "Need complete renderer", "reason": "missing source",
             "missing_context": ["renderCards"], "operations": []},
            {"status": "ready", "summary": "Updated label", "operations": [{"find": "baseline", "replace": "updated"}]},
        ]

        def fake_run(args):
            if args[1:] == ["diff", "--name-only"]:
                return "index.html"
            if args[1:] == ["diff", "--", "index.html"]:
                return "diff --git a/index.html b/index.html\n-baseline\n+updated\n"
            raise AssertionError(f"Unexpected git command in test: {args}")

        with patch.object(orchestrator, "ROOT", root), \
             patch.object(orchestrator, "TARGET_FILE", "index.html"), \
             patch.object(orchestrator, "check_clean_tree"), \
             patch.object(orchestrator, "build_context", side_effect=[initial_context, expanded_context]) as context_builder, \
             patch.object(orchestrator, "propose", side_effect=proposals) as code_agent, \
             patch.object(orchestrator, "apply_operations", return_value="updated source"), \
             patch.object(orchestrator, "enforce_sensitive_guard"), \
             patch.object(orchestrator, "enforce_file_allowlist"), \
             patch.object(orchestrator, "review", return_value={"verdict": "pass", "findings": []}), \
             patch.object(orchestrator, "run", side_effect=fake_run):
            with patch.dict("os.environ", {"TASK": "Update a label", "ALLOW_SENSITIVE_CHANGES": "false"}):
                orchestrator.main()

        _assert(context_builder.call_count == 2, "orchestrator did not rebuild context")
        _assert(context_builder.call_args.kwargs.get("extra_names") == ["renderCards"],
                "orchestrator did not request the exact missing symbol")
        _assert(code_agent.call_count == 2, "Code Agent was not retried after context expansion")
        _assert("CONTEXT RECOVERY" in code_agent.call_args_list[1].args[0],
                "retried task did not explain the expanded context")
        _assert(code_agent.call_args_list[1].args[1] == expanded_context["context"],
                "retried Code Agent did not receive expanded source context")
        _assert(target.read_text(encoding="utf-8") == "updated source",
                "successful patch was not retained after independent QA")


def test_deepseek_retries_transient_http_errors() -> None:
    import io
    import json
    import urllib.error
    import deepseek_client
    from unittest.mock import patch

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({
                "usage": {},
                "choices": [{"finish_reason": "stop", "message": {"content": "{\"ok\":true}"}}],
            }).encode("utf-8")

    transient = urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions", 429, "rate limited", {}, io.BytesIO(b"try later")
    )
    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
         patch.object(deepseek_client, "API_MAX_ATTEMPTS", 2), \
         patch.object(deepseek_client.urllib.request, "urlopen", side_effect=[transient, FakeResponse()]) as urlopen, \
         patch.object(deepseek_client.time, "sleep"):
        result = deepseek_client.chat("system", "user")
    _assert(result == {"ok": True}, f"unexpected retry result: {result}")
    _assert(urlopen.call_count == 2, "transient 429 response was not retried exactly once")


def test_maintenance_allowlist_and_workflow_guards() -> None:
    from agent import maintenance_agent
    _assert(maintenance_agent._allowed("agent/orchestrator.py"), "orchestrator must be repairable")
    _assert(maintenance_agent._allowed(".github/workflows/ai-issue.yml"), "issue workflow must be repairable")
    _assert(maintenance_agent._allowed(".github/workflows/ci.yml"), "CI workflow must be repairable")
    _assert(not maintenance_agent._allowed("index.html"), "product code must never be maintenance-editable")
    _assert(not maintenance_agent._allowed("requirements.txt"), "dependency changes must remain manual")
    _assert(not maintenance_agent._allowed("agent/maintenance_agent.py"), "maintenance agent must not self-modify")
    _assert(not maintenance_agent._allowed("agent/patch_validator.py"), "patch security validator must be read-only")
    _assert(not maintenance_agent._allowed("agent/qa_agent.py"), "independent QA prompt must be read-only")
    _assert(maintenance_agent.REVIEW_MODEL == "deepseek-v4-pro", "maintenance review must use the separate Pro model by default")
    original_chat = maintenance_agent.chat
    review_call = {}
    def fake_review_chat(system, user, **kwargs):
        review_call.update(kwargs)
        return {"verdict": "pass", "findings": []}
    maintenance_agent.chat = fake_review_chat
    try:
        maintenance_agent._independent_review("failure", "diff")
    finally:
        maintenance_agent.chat = original_chat
    _assert(review_call.get("model") == "deepseek-v4-pro", "review must use a different model than the Flash repair agent")
    _assert(review_call.get("thinking") == "enabled", "review must enable reasoning mode")
    valid = (ROOT / ".github/workflows/ai-issue.yml").read_text(encoding="utf-8")
    maintenance_agent._workflow_invariants(".github/workflows/ai-issue.yml", valid)
    issue_workflow = (ROOT / ".github/workflows/ai-issue.yml").read_text(encoding="utf-8")
    dev_workflow = (ROOT / ".github/workflows/ai-development.yml").read_text(encoding="utf-8")
    _assert("set -o pipefail\n          python -B agent/clarifier.py" in issue_workflow, "clarifier pipeline must propagate failures")
    _assert("set -o pipefail\n          python -B tests/smoke_test.py" in issue_workflow, "issue pre-smoke pipeline must propagate failures")
    _assert("set -o pipefail\n          python -B tests/smoke_test.py" in dev_workflow, "development pre-smoke pipeline must propagate failures")
    maintenance_agent._source_invariants("agent/orchestrator.py", (ROOT / "agent/orchestrator.py").read_text(encoding="utf-8"))
    maintenance_agent._source_invariants("agent/context_builder.py", (ROOT / "agent/context_builder.py").read_text(encoding="utf-8"))
    maintenance_agent._source_invariants("agent/deepseek_client.py", (ROOT / "agent/deepseek_client.py").read_text(encoding="utf-8"))
    maintenance_agent._source_invariants("agent/task_refiner.py", (ROOT / "agent/task_refiner.py").read_text(encoding="utf-8"))
    maintenance_agent._source_invariants("tests/smoke_test.py", (ROOT / "tests/smoke_test.py").read_text(encoding="utf-8"))
    dev_workflow = (ROOT / ".github/workflows/ai-development.yml").read_text(encoding="utf-8")
    maintenance_agent._workflow_invariants(".github/workflows/ai-development.yml", dev_workflow)
    ci_workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    maintenance_agent._workflow_invariants(".github/workflows/ci.yml", ci_workflow)
    _assert("pull-requests: write" not in ci_workflow, "PR CI workflow must stay read-only")
    permissions = maintenance_agent._permission_stanza(ci_workflow)
    safe_ci_edit = ci_workflow.replace("All workflow YAML and run-step shell syntax checks passed.",
                                       "All workflow YAML and run-step shell syntax checks passed; safe edit.")
    elevated_ci = ci_workflow.replace("  contents: read", "  contents: write", 1)
    _assert(permissions == maintenance_agent._permission_stanza(safe_ci_edit),
            "non-permission CI edit should preserve the permissions stanza")
    _assert(permissions != maintenance_agent._permission_stanza(elevated_ci),
            "permission escalation was not detected by stanza comparison")
    try:
        maintenance_agent._workflow_invariants(".github/workflows/ci.yml", ci_workflow.replace("python -B tests/smoke_test.py", "echo skipped"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("CI workflow passed after its smoke test gate was removed")
    settings_source = (ROOT / "agent/settings.py").read_text(encoding="utf-8")
    _assert('MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")' in settings_source,
            "default DeepSeek model is not a currently supported model ID")
    _assert("deepseek-chat" not in valid + dev_workflow,
            "workflow still references the retired deepseek-chat model ID")
    try:
        maintenance_agent._workflow_invariants(".github/workflows/ai-issue.yml", valid + "\n# --admin\n")
    except RuntimeError:
        pass
    else:
        raise AssertionError("workflow privilege escalation marker passed invariant check")


def test_truncation_is_not_infrastructure_failure() -> None:
    """finish_reason=length must never set maintenance_eligible=true."""
    import json
    import deepseek_client
    import orchestrator
    from unittest.mock import patch

    class TruncatedResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({
                "usage": {"completion_tokens": 3500},
                "choices": [{"finish_reason": "length", "message": {"content": "{\"operations\":"}}],
            }).encode("utf-8")

    captured = {}

    def fake_write(maintenance_eligible, failure_class):
        captured["eligible"] = str(maintenance_eligible).lower()
        captured["class"] = failure_class

    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
         patch.object(deepseek_client, "API_MAX_ATTEMPTS", 1), \
         patch.object(deepseek_client, "TRUNCATION_RETRY_MAX", 0), \
         patch.object(deepseek_client.urllib.request, "urlopen", return_value=TruncatedResponse()), \
         patch.object(orchestrator, "_write_github_outputs", side_effect=fake_write), \
         patch.object(orchestrator, "_run_pipeline",
                      side_effect=deepseek_client.DeepSeekTruncationError("truncated")):
        try:
            orchestrator.main()
        except deepseek_client.DeepSeekTruncationError:
            pass
        else:
            raise AssertionError("truncation must propagate to the workflow")

    _assert(captured.get("eligible") == "false",
            f"truncation wrongly eligible for maintenance: {captured}")
    _assert(captured.get("class") == orchestrator.FAILURE_TRUNCATED_OUTPUT,
            f"truncation misclassified: {captured}")


def test_code_agent_truncation_retries_once() -> None:
    """A truncated Code Agent response triggers exactly one concise retry."""
    import json
    import deepseek_client
    from unittest.mock import patch

    class TruncatedResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({
                "usage": {"completion_tokens": 3500},
                "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
            }).encode("utf-8")

    class CompleteResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({
                "usage": {"completion_tokens": 900},
                "choices": [{"finish_reason": "stop", "message": {"content": "{\"status\":\"ready\"}"}}],
            }).encode("utf-8")

    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
         patch.object(deepseek_client, "API_MAX_ATTEMPTS", 1), \
         patch.object(deepseek_client, "TRUNCATION_RETRY_MAX", 1), \
         patch.object(deepseek_client.urllib.request, "urlopen",
                      side_effect=[TruncatedResponse(), CompleteResponse()]) as urlopen, \
         patch.object(deepseek_client.time, "sleep"):
        result = deepseek_client.chat("system", "user", max_tokens=3500, retry_on_truncation=True)

    _assert(result == {"status": "ready"}, f"retry did not recover: {result}")
    _assert(urlopen.call_count == 2, "truncation was not retried exactly once")


def test_truncation_retry_is_opt_in_only() -> None:
    """QA, refiner and maintenance calls must never retry on truncation."""
    import json
    import deepseek_client
    from unittest.mock import patch

    class TruncatedResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps({
                "usage": {"completion_tokens": 900},
                "choices": [{"finish_reason": "length", "message": {"content": "partial"}}],
            }).encode("utf-8")

    with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), \
         patch.object(deepseek_client, "API_MAX_ATTEMPTS", 1), \
         patch.object(deepseek_client, "TRUNCATION_RETRY_MAX", 1), \
         patch.object(deepseek_client.urllib.request, "urlopen",
                      return_value=TruncatedResponse()) as urlopen, \
         patch.object(deepseek_client.time, "sleep"):
        try:
            deepseek_client.chat("system", "user", max_tokens=900)
        except deepseek_client.DeepSeekTruncationError:
            pass
        else:
            raise AssertionError("non-opt-in call must raise on truncation")

    _assert(urlopen.call_count == 1, "non-opt-in call performed an extra request")
    _assert(deepseek_client.TRUNCATION_RETRY_MAX <= 1,
            "truncation retry ceiling must stay at most 1")


def test_incomplete_response_never_modifies_file() -> None:
    """A truncated proposal must not reach apply_operations or the working tree."""
    import tempfile
    import orchestrator
    from unittest.mock import patch

    with tempfile.TemporaryDirectory(prefix="altcards-truncation-") as td:
        root = pathlib.Path(td)
        target = root / "index.html"
        baseline = "baseline source"
        target.write_text(baseline, encoding="utf-8")
        context = {"context": "BASE CONTEXT", "chars": 12, "included": ["base"],
                   "missing": [], "over_limit": [], "ok": True}

        with patch.object(orchestrator, "ROOT", root), \
             patch.object(orchestrator, "TARGET_FILE", "index.html"), \
             patch.object(orchestrator, "check_clean_tree"), \
             patch.object(orchestrator, "build_context", return_value=context), \
             patch.object(orchestrator, "propose",
                          side_effect=orchestrator.DeepSeekTruncationError("hit max_tokens")), \
             patch.object(orchestrator, "apply_operations") as apply_ops:
            with patch.dict("os.environ", {"TASK": "Update a label", "ALLOW_SENSITIVE_CHANGES": "false"}):
                try:
                    orchestrator.main()
                except orchestrator.DeepSeekTruncationError:
                    pass
                else:
                    raise AssertionError("truncation must abort the pipeline")

        _assert(apply_ops.call_count == 0, "truncated proposal reached the patch applicator")
        _assert(target.read_text(encoding="utf-8") == baseline,
                "truncated response modified the target file")


def test_infrastructure_error_still_eligible_for_maintenance() -> None:
    """A real DeepSeekInfrastructureError must keep the self-repair path open."""
    import orchestrator
    from unittest.mock import patch

    captured = {}

    def fake_write(maintenance_eligible, failure_class):
        captured["eligible"] = str(maintenance_eligible).lower()
        captured["class"] = failure_class

    with patch.object(orchestrator, "_write_github_outputs", side_effect=fake_write), \
         patch.object(orchestrator, "_run_pipeline",
                      side_effect=orchestrator.DeepSeekInfrastructureError("upstream 503")):
        try:
            orchestrator.main()
        except orchestrator.DeepSeekInfrastructureError:
            pass
        else:
            raise AssertionError("infrastructure error must propagate")

    _assert(captured.get("eligible") == "true",
            f"infrastructure failure lost maintenance eligibility: {captured}")
    _assert(captured.get("class") == orchestrator.FAILURE_INFRASTRUCTURE,
            f"infrastructure failure misclassified: {captured}")


def test_ordinary_qa_failure_is_not_eligible_for_maintenance() -> None:
    """A plain QA rejection must not start infrastructure self-repair."""
    import orchestrator
    from unittest.mock import patch

    captured = {}

    def fake_write(maintenance_eligible, failure_class):
        captured["eligible"] = str(maintenance_eligible).lower()
        captured["class"] = failure_class

    with patch.object(orchestrator, "_write_github_outputs", side_effect=fake_write), \
         patch.object(orchestrator, "_run_pipeline",
                      side_effect=RuntimeError("All 3 attempts failed. Last verdict: reject.")):
        try:
            orchestrator.main()
        except RuntimeError:
            pass
        else:
            raise AssertionError("QA failure must propagate")

    _assert(captured.get("eligible") == "false",
            f"QA failure wrongly eligible for maintenance: {captured}")
    _assert(captured.get("class") == orchestrator.FAILURE_NON_INFRASTRUCTURE,
            f"QA failure misclassified: {captured}")


def test_maintenance_gate_requires_explicit_output() -> None:
    """The issue workflow must gate self-repair on an explicit output, not failure()."""
    from agent import maintenance_agent
    workflow = (ROOT / ".github/workflows/ai-issue.yml").read_text(encoding="utf-8")
    start = workflow.index("- name: Self-repair automation infrastructure")
    end = workflow.index("- name: Commit and merge infrastructure repair")
    block = workflow[start:end]
    _assert("steps.agent.outputs.maintenance_eligible == 'true'" in block,
            "maintenance step is not gated on the explicit orchestrator output")
    condition = block.split("run:", 1)[0]
    _assert("failure()" not in condition,
            "maintenance step still contains a generic failure() fallback")
    maintenance_agent._workflow_invariants(".github/workflows/ai-issue.yml", workflow)


def main() -> int:
    tests = [
        test_brace_matcher_handles_templates_and_nested,
        test_sensitive_guard_requires_explicit_approval,
        test_validation_failure_refines_next_instruction,
        test_generic_xp_task_is_complete,
        test_ranking_bonus_task,
        test_login_task,
        test_card_management_context_has_full_dependencies,
        test_agent_prompts_receive_complete_task,
        test_context_builder_targeted_expansion,
        test_need_context_symbol_extraction,
        test_orchestrator_recovers_need_context,
        test_deepseek_retries_transient_http_errors,
        test_maintenance_allowlist_and_workflow_guards,
        test_truncation_is_not_infrastructure_failure,
        test_code_agent_truncation_retries_once,
        test_truncation_retry_is_opt_in_only,
        test_incomplete_response_never_modifies_file,
        test_infrastructure_error_still_eligible_for_maintenance,
        test_ordinary_qa_failure_is_not_eligible_for_maintenance,
        test_maintenance_gate_requires_explicit_output,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
