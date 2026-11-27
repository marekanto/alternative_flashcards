"""
agent/orchestrator.py — AltCards

Pipeline z samodoskonalącym się feedbackiem QA:
  1. clean tree check,
  2. kontekst z ORIGINAL_TASK (raz na cały run),
  3. pętla: Code Agent → diff → QA vs ORIGINAL_TASK → refiner → retry.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from code_agent import propose
from deepseek_client import DeepSeekInfrastructureError, DeepSeekTruncationError
from context_builder import (
    build_context,
    MAX_CONTEXT_CHARS,
    _positive_scope_for_classification,
)
from patch_validator import (
    SensitiveChangeNotApproved,
    apply_operations,
    enforce_file_allowlist,
    enforce_sensitive_guard,
)
from qa_agent import review
from task_refiner import refine_task
from settings import TARGET_FILE

ROOT = Path(__file__).resolve().parents[1]
QA_MAX_ATTEMPTS = max(1, int(os.getenv("QA_MAX_ATTEMPTS", "3")))
CONTEXT_REBUILD_MAX = max(0, int(os.getenv("CONTEXT_REBUILD_MAX", "2")))
DIAGNOSE_ONLY = os.getenv("DIAGNOSE_ONLY", "false").lower() == "true"
TRULY_TERMINAL = {"cannot_do_safely", "refused"}

# Failure classes published to GitHub Actions. Only "infrastructure" may set
# maintenance_eligible=true; every other class is explicitly false.
FAILURE_INFRASTRUCTURE = "infrastructure"
FAILURE_TRUNCATED_OUTPUT = "truncated_output"
FAILURE_NON_INFRASTRUCTURE = "non_infrastructure"
FAILURE_NONE = "none"


def run(args):
    return subprocess.run(
        args, cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout


def check_clean_tree():
    status = run(["git", "status", "--porcelain", "--untracked-files=all"])
    dirty = []
    for line in status.splitlines():
        path = line[3:].split(" -> ")[-1].strip()
        if "__pycache__/" in path or path.endswith(".pyc"):
            continue
        dirty.append(line)
    if dirty:
        raise RuntimeError(
            "Checkout is not clean before agent run. Unexpected entries:\n"
            + "\n".join(dirty[:30])
        )


def _extract_operations(proposal):
    if not isinstance(proposal, dict):
        return []
    for key in ("operations", "edits", "patches", "changes"):
        val = proposal.get(key)
        if isinstance(val, list) and val:
            return val
    return []


def _refine_after_failure(original_task, current_task, failure, previous_diff, attempt):
    """Turn a concrete patch-generation/validation failure into the next instruction."""
    feedback = [f"Patch attempt {attempt} failed validation: {failure}"]
    refined = refine_task(
        original_task,
        feedback,
        previous_diff or "(Patch rejected before a diff was created.)",
        attempt,
    )
    if refined.get("ok") and refined.get("refined_task"):
        print(f"  → Refiner: {refined.get('reasoning', 'task refined')}")
        return refined["refined_task"]

    # Even if the refiner is unavailable, do not repeat the identical prompt.
    print(f"  → Refiner unavailable: {refined.get('reasoning', 'unknown error')}")
    return (
        current_task
        + "\n\nThe previous patch was rejected by deterministic validation. "
        + "Correct this exact problem without weakening safeguards:\n- "
        + str(failure)
    )


def _requested_context_names(proposal, task):
    """Extract required symbols from the model response, not task prose.

    Task prose names protected and out-of-scope symbols for context. Treating
    every backticked identifier as a dependency caused recovery to request
    unrelated functions such as ans/grade. HTML tags/comments remain supported
    because tasks can explicitly target literal markup.
    """
    narrative_values = []
    direct_names = []
    if isinstance(proposal, dict):
        names = proposal.get("missing_context", [])
        if isinstance(names, list):
            direct_names = [str(x).strip() for x in names[:12]]
        narrative_values.extend(
            str(proposal.get(k, ""))
            for k in ("reason", "summary", "diagnosis")
        )

    candidates = []
    html_tag_pattern = re.compile(r"</\s*(body|head|html)\s*>", re.I)
    html_comment_pattern = re.compile(r"<!--[\s\S]{0,500}?-->")

    for name in direct_names:
        candidates.extend(f"</{match.group(1).lower()}>" for match in html_tag_pattern.finditer(name))
        candidates.extend(match.group(0) for match in html_comment_pattern.finditer(name))
        if re.fullmatch(r"[A-Za-z_$][\w$]{0,80}", name):
            candidates.append(name)

    # Model-authored explanation can name exact missing symbols. Never scan
    # task text for function identifiers: it includes requirements/exclusions,
    # not a list of source dependencies.
    for value in narrative_values:
        candidates.extend(re.findall(r"[\x60]([A-Za-z_$][\w$]{0,80})[\x60]", value))
        candidates.extend(re.findall(r"\b(?:function|method|symbol|handler)\s+([A-Za-z_$][\w$]{0,80})", value, re.I))

    task_scope = _positive_scope_for_classification(task)
    candidates.extend(f"</{match.group(1).lower()}>" for match in html_tag_pattern.finditer(task_scope))
    candidates.extend(match.group(0) for match in html_comment_pattern.finditer(task_scope))

    common = {"need_context", "context", "function", "method", "source", "missing", "undefined", "return", "const", "async"}
    return list(dict.fromkeys(x for x in candidates if x.lower() not in common))[:12]

def _print_diagnosis(proposal):
    print("\n" + "=" * 70)
    print("DIAGNOSE MODE — analiza modelu (patch NIE zaaplikowany)")
    print("=" * 70)
    print(f"\nStatus:  {(proposal or {}).get('status')}")
    print(f"Summary: {(proposal or {}).get('summary', '')}")
    for key in ("reasoning", "analysis", "explanation", "notes", "diagnosis"):
        val = (proposal or {}).get(key)
        if val:
            print(f"\n--- {key} ---\n{val}")
    ops = _extract_operations(proposal)
    if ops:
        print(f"\n--- Proponowane operacje ({len(ops)}; nieaplikowane) ---")
        for i, op in enumerate(ops[:10], 1):
            if isinstance(op, dict):
                f = str(op.get("find", ""))[:120].replace("\n", " ⏎ ")
                r = str(op.get("replace", ""))[:120].replace("\n", " ⏎ ")
                print(f"  {i}. FIND: {f!r}")
                print(f"     REPL: {r!r}")


def _run_pipeline():
    """Return normally on a QA-approved patch; raise on any failure."""
    task = os.getenv("TASK", "").strip()
    if not task or len(task) > 4000:
        raise RuntimeError("TASK must contain 1–4000 characters")
    allow_sensitive = (
        os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"
    )

    check_clean_tree()

    source_path = ROOT / TARGET_FILE
    if not source_path.exists():
        raise RuntimeError(f"Target file not found: {TARGET_FILE}")
    source = source_path.read_text(encoding="utf-8")

    ctx_result = build_context(task, source)
    base_context = ctx_result["context"]

    print(
        f"Context selected: {ctx_result['chars']} chars "
        f"({len(ctx_result['included'])} blocks); index.html: {len(source)} chars"
    )
    print(f"Mode: {'DIAGNOSE_ONLY' if DIAGNOSE_ONLY else 'PATCH'}")

    if ctx_result["over_limit"]:
        raise RuntimeError(
            f"Context truncated (limit={MAX_CONTEXT_CHARS}). "
            f"Over limit: {ctx_result['over_limit']}."
        )
    if ctx_result["missing"]:
        raise RuntimeError(
            f"Context missing blocks: {ctx_result['missing']}."
        )

    if DIAGNOSE_ONLY:
        _print_diagnosis(propose(task, base_context))
        return

    current_task = task
    original_task = task
    qa_history: list[list[str]] = []
    previous_diff = ""
    last_verdict = None
    last_error = "brak"

    patch_attempt = 0
    context_rebuilds = 0
    while patch_attempt < QA_MAX_ATTEMPTS:
        print(f"\n=== Patch attempt {patch_attempt + 1}/{QA_MAX_ATTEMPTS} "
              f"(context rebuilds: {context_rebuilds}/{CONTEXT_REBUILD_MAX}) ===")
        if patch_attempt or context_rebuilds:
            print(f"Task for agent (refined): {current_task[:240]}...")

        proposal = propose(current_task, base_context)
        status = (proposal or {}).get("status")
        summary = (proposal or {}).get("summary", "")
        print(f"Code agent: {status} — {summary}")

        if status == "need_context":
            if context_rebuilds >= CONTEXT_REBUILD_MAX:
                raise RuntimeError(
                    f"Code agent still needs context after {CONTEXT_REBUILD_MAX} "
                    f"rebuilds. Summary: {summary}. Reason: {(proposal or {}).get('reason', '')}"
                )
            names = _requested_context_names(proposal, original_task)
            expanded = build_context(original_task, source, extra_names=names)
            new_context = expanded["context"]
            if not expanded.get("ok", False):
                # A failed extraction is not papered over. Stop instead of asking
                # the model to guess at missing or truncated code.
                raise RuntimeError(
                    "Code agent requested source symbols that could not be resolved. "
                    f"Names={names}; missing={expanded.get('missing')}; "
                    f"over_limit={expanded.get('over_limit')}"
                )

            context_rebuilds += 1
            if new_context == base_context:
                # The requested symbols are already in the supplied context.
                # Give the model one bounded chance to re-read them instead of
                # treating a redundant request as a missing-source failure.
                current_task = (
                    current_task
                    + "\n\nCONTEXT CHECK: The symbols you requested are already present "
                    + "in the supplied source context. Re-read those snippets and continue; "
                    + "do not request the same symbols again. Request only an essential "
                    + "source symbol that is genuinely absent."
                )
                print(f"  → Requested context already present: {names or '(no new symbols)'}")
                continue

            base_context = new_context
            current_task = (
                current_task
                + "\n\nCONTEXT RECOVERY: The previous response requested more source context. "
                + "Use the expanded, current source snippets below. Do not infer missing code. "
                + ("Requested symbols: " + ", ".join(names) if names else "Re-check the complete supplied snippets.")
            )
            print(f"  → Context expanded with: {names or '(no new symbols)'}")
            continue

        if status in TRULY_TERMINAL:
            raise RuntimeError(
                f"Code agent returned terminal status={status!r}. "
                f"Summary: {summary}. No automatic retry is allowed for a safety refusal."
            )

        patch_attempt += 1
        ops = _extract_operations(proposal)
        if not ops:
            last_error = f"no operations (status={status!r})"
            print(f"  → {last_error}")
            if patch_attempt < QA_MAX_ATTEMPTS:
                current_task = _refine_after_failure(
                    original_task, current_task, last_error, previous_diff, patch_attempt
                )
            continue

        try:
            updated = apply_operations(source, proposal)
        except Exception as e:
            last_error = f"patch validation: {e}"
            print(f"  → {last_error}; retrying with validation feedback")
            if patch_attempt < QA_MAX_ATTEMPTS:
                current_task = _refine_after_failure(
                    original_task, current_task, last_error, previous_diff, patch_attempt
                )
            continue

        try:
            enforce_sensitive_guard(source, updated, allow_sensitive)
        except SensitiveChangeNotApproved as e:
            raise RuntimeError(
                f"{e} Workflow stopped without retrying. "
                "Obtain explicit approval for this Issue before rerunning."
            ) from e

        source_path.write_text(updated, encoding="utf-8")
        try:
            changed = set(run(["git", "diff", "--name-only"]).splitlines())
            enforce_file_allowlist(changed)
            diff = run(["git", "diff", "--", TARGET_FILE])
        except Exception as e:
            source_path.write_text(source, encoding="utf-8")
            raise RuntimeError(f"Patch touched a forbidden path or diff check failed: {e}") from e

        if not diff.strip():
            source_path.write_text(source, encoding="utf-8")
            last_error = "no diff produced"
            print(f"  → {last_error}; retrying")
            continue

        try:
            verdict = review(original_task, diff)
        except Exception:
            source_path.write_text(source, encoding="utf-8")
            raise
        last_verdict = verdict
        qa_verdict = verdict.get("verdict") if isinstance(verdict, dict) else None
        findings = verdict.get("findings", []) if isinstance(verdict, dict) else []
        print(f"QA verdict: {qa_verdict}")
        for f in findings[:8] if isinstance(findings, list) else []:
            print("QA:", f)

        if qa_verdict == "pass":
            print(f"\n✓ QA passed on patch attempt {patch_attempt}")
            print("AI patch passed static validation and independent review.")
            print("SUMMARY:", summary or "AltCards change")
            return
        if qa_verdict == "needs_human_review":
            source_path.write_text(source, encoding="utf-8")
            raise RuntimeError(
                "Independent QA requested human review. Patch was discarded; "
                f"findings: {findings}"
            )

        qa_history.append(findings if isinstance(findings, list) else [str(findings)])
        previous_diff = diff
        source_path.write_text(source, encoding="utf-8")
        last_error = f"QA {qa_verdict}"

        if patch_attempt < QA_MAX_ATTEMPTS:
            all_findings = [f for r in qa_history for f in r]
            refined = refine_task(original_task, all_findings, previous_diff, patch_attempt)
            if refined.get("ok"):
                print(f"  → Refiner: {refined['reasoning']}")
                current_task = refined["refined_task"]
            else:
                print(f"  → Refiner failed: {refined.get('reasoning', 'unknown')}")
                current_task = (
                    original_task + "\n\nQA rejected the previous patch. Fix only these concrete findings:\n"
                    + "\n".join(f"- {f}" for f in all_findings)
                )

    raise RuntimeError(
        f"All {QA_MAX_ATTEMPTS} attempts failed. "
        f"Last verdict: {last_verdict and last_verdict.get('verdict')}. "
        f"Last error: {last_error}. "
        "Try DIAGNOSE_ONLY=true or narrow the original task."
    )


def main():
    """Run the pipeline and publish an explicit recovery decision to the workflow."""
    try:
        _run_pipeline()
    except DeepSeekInfrastructureError as exc:
        _write_github_outputs(True, FAILURE_INFRASTRUCTURE)
        print("ERROR_CLASS=" + FAILURE_INFRASTRUCTURE)
        print("MAINTENANCE_ELIGIBLE=true")
        print("ERROR:", str(exc), file=sys.stderr)
        raise
    except DeepSeekTruncationError as exc:
        # The API and transport worked; the request was too large for the output
        # ceiling. Repository code cannot repair that, so the workflow may retry
        # the task but must never start infrastructure maintenance.
        _write_github_outputs(False, FAILURE_TRUNCATED_OUTPUT)
        print("ERROR_CLASS=" + FAILURE_TRUNCATED_OUTPUT)
        print("MAINTENANCE_ELIGIBLE=false")
        print("ERROR:", str(exc), file=sys.stderr)
        raise
    except Exception as exc:
        _write_github_outputs(False, FAILURE_NON_INFRASTRUCTURE)
        print("ERROR_CLASS=" + FAILURE_NON_INFRASTRUCTURE)
        print("MAINTENANCE_ELIGIBLE=false")
        print("ERROR:", str(exc), file=sys.stderr)
        raise
    _write_github_outputs(False, FAILURE_NONE)
    print("ERROR_CLASS=" + FAILURE_NONE)
    print("MAINTENANCE_ELIGIBLE=false")


def _write_github_outputs(maintenance_eligible: bool, failure_class: str):
    """Publish an explicit, conservative recovery decision to GitHub Actions."""
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"maintenance_eligible={str(maintenance_eligible).lower()}\n")
        output.write(f"failure_class={failure_class}\n")


if __name__ == "__main__":
    # main() already classified the failure and wrote the GitHub outputs.
    try:
        main()
    except BaseException:
        sys.exit(1)
