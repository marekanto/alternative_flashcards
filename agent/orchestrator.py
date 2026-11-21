"""
agent/orchestrator.py — AltCards

Pipeline z samodoskonalącym się feedbackiem QA:
  1. clean tree check,
  2. kontekst z ORIGINAL_TASK (raz na cały run),
  3. pętla: Code Agent → diff → QA vs ORIGINAL_TASK → refiner → retry.
"""
import os
import subprocess
import sys
from pathlib import Path

from code_agent import propose
from context_builder import build_context, MAX_CONTEXT_CHARS
from patch_validator import (
    apply_operations,
    enforce_file_allowlist,
    enforce_sensitive_guard,
)
from qa_agent import review
from task_refiner import refine_task
from settings import TARGET_FILE

ROOT = Path(__file__).resolve().parents[1]
QA_MAX_ATTEMPTS = int(os.getenv("QA_MAX_ATTEMPTS", "3"))
DIAGNOSE_ONLY = os.getenv("DIAGNOSE_ONLY", "false").lower() == "true"
TRULY_TERMINAL = {"need_context", "refused"}


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


def main():
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

    for attempt in range(1, QA_MAX_ATTEMPTS + 1):
        print(f"\n=== Attempt {attempt}/{QA_MAX_ATTEMPTS} ===")
        if attempt > 1:
            print(f"Task for agent (refined): {current_task[:200]}...")

        proposal = propose(current_task, base_context)
        status = (proposal or {}).get("status")
        summary = (proposal or {}).get("summary", "")
        print(f"Code agent: {status} — {summary}")

        if status in TRULY_TERMINAL:
            raise RuntimeError(
                f"Code agent returned terminal status={status!r}. "
                f"Summary: {summary}. Use DIAGNOSE_ONLY=true to inspect."
            )

        ops = _extract_operations(proposal)
        if not ops:
            last_error = f"no operations (status={status!r})"
            print(f"  → {last_error}")
            if qa_history or attempt == 1:
                refined = refine_task(
                    original_task,
                    [last_error],
                    previous_diff or "(no previous diff)",
                    attempt,
                )
                if refined["ok"]:
                    print(f"  → Refined task: {refined['reasoning']}")
                    current_task = refined["refined_task"]
                else:
                    print(f"  → Refiner failed: {refined['reasoning']}")
            continue

        try:
            updated = apply_operations(source, proposal)
            enforce_sensitive_guard(source, updated, allow_sensitive)
        except Exception as e:
            last_error = f"patch validation: {e}"
            print(f"  → {last_error}; retrying")
            continue

        source_path.write_text(updated, encoding="utf-8")
        try:
            changed = set(run(["git", "diff", "--name-only"]).splitlines())
            enforce_file_allowlist(changed)
            diff = run(["git", "diff", "--", TARGET_FILE])
        except Exception as e:
            source_path.write_text(source, encoding="utf-8")
            last_error = f"diff/allowlist: {e}"
            print(f"  → {last_error}; retrying")
            continue

        if not diff.strip():
            source_path.write_text(source, encoding="utf-8")
            last_error = "no diff produced"
            print(f"  → {last_error}; retrying")
            continue

        verdict = review(original_task, diff)
        last_verdict = verdict
        qa_verdict = verdict.get("verdict")
        findings = verdict.get("findings", [])
        print(f"QA verdict: {qa_verdict}")
        for f in findings[:8]:
            print("QA:", f)

        if qa_verdict == "pass":
            print(f"\n✓ QA passed on attempt {attempt}")
            print("AI patch passed static validation and independent review.")
            print("SUMMARY:", summary or "AltCards change")
            return

        qa_history.append(findings)
        previous_diff = diff

        if attempt < QA_MAX_ATTEMPTS:
            all_findings = [f for r in qa_history for f in r]
            refined = refine_task(
                original_task, all_findings, previous_diff, attempt
            )
            if refined["ok"]:
                print(f"  → Refiner: {refined['reasoning']}")
                current_task = refined["refined_task"]
                print(f"  → Next task: {current_task[:300]}...")
            else:
                print(f"  → Refiner failed: {refined['reasoning']}")
                current_task = (
                    original_task
                    + "\n\nQA said the previous attempt was wrong. Fix these:\n"
                    + "\n".join(f"- {f}" for f in all_findings)
                )

        source_path.write_text(source, encoding="utf-8")
        last_error = f"QA {qa_verdict}"

    raise RuntimeError(
        f"All {QA_MAX_ATTEMPTS} attempts failed. "
        f"Last verdict: {last_verdict and last_verdict.get('verdict')}. "
        f"Last error: {last_error}. "
        "Try DIAGNOSE_ONLY=true or narrow the original task."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
