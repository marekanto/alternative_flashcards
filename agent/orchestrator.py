"""
agent/orchestrator.py — AltCards

Pipeline:
  1. clean tree check,
  2. kontekst XP z context_builder,
  3. Code Agent → operations / analiza,
  4. patch_validator,
  5. QA review z retry loop.

Statusy agenta:
  * ready / ok / applied / patch   → próbujemy patch
  * cannot_do_safely, need_context,
    refused, needs_human_review,
    error                          → TERMINALNE (stop, bez retry)
  * wszystko inne + brak ops       → 1 retry, potem stop

Tryby:
  * DIAGNOSE_ONLY=true → model NIE patchuje, tylko analizuje.
    Wypisuje summary + reasoning + proponowane operacje (nieaplikowane).
    Nie uruchamia QA review. Idealne do zrozumienia przyczyny.

QA review:
  * pass                 → sukces
  * needs_human_review   → terminalne, przerwij
  * fail                 → retry z feedbackiem (max QA_MAX_ATTEMPTS)
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
from settings import TARGET_FILE

ROOT = Path(__file__).resolve().parents[1]

QA_MAX_ATTEMPTS = int(os.getenv("QA_MAX_ATTEMPTS", "2"))
DIAGNOSE_ONLY = os.getenv("DIAGNOSE_ONLY", "false").lower() == "true"

# Statusy, przy których dalsze próby nie mają sensu — model odmówił,
# poprosił o kontekst, albo QA zażądał człowieka.
TERMINAL_STATUSES = {
    "need_context",
    "refused",
    "error",
    "cannot_do_safely",
    "needs_human_review",
}


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
        details = "\n".join(dirty[:30])
        raise RuntimeError(
            "Checkout is not clean before agent run. "
            "Unexpected working-tree entries:\n"
            + details
            + "\nCommit, move, or remove these entries before rerunning."
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
    """Wypisuje pełną analizę modelu (tryb DIAGNOSE_ONLY)."""
    print("\n" + "=" * 70)
    print("DIAGNOSE MODE — analiza modelu (patch NIE został zaaplikowany)")
    print("=" * 70)

    status = (proposal or {}).get("status")
    print(f"\nStatus:  {status}")
    print(f"Summary: {(proposal or {}).get('summary', '')}")

    # model może zwrócić reasoning / analysis / explanation / notes
    for key in ("reasoning", "analysis", "explanation", "notes", "diagnosis"):
        val = (proposal or {}).get(key)
        if val:
            print(f"\n--- {key} ---")
            print(val if isinstance(val, str) else repr(val))

    ops = _extract_operations(proposal)
    if ops:
        print(f"\n--- Proponowane operacje ({len(ops)}; NIE zaaplikowane) ---")
        for i, op in enumerate(ops[:10], 1):
            if isinstance(op, dict):
                find_txt = str(op.get("find", ""))[:120].replace("\n", " ⏎ ")
                repl_txt = str(op.get("replace", ""))[:120].replace("\n", " ⏎ ")
                print(f"  {i}. FIND: {find_txt!r}")
                print(f"     REPL: {repl_text!r}" if False else f"     REPL: {repl_txt!r}")
            else:
                print(f"  {i}. {str(op)[:160]}")


def main():
    task = os.getenv("TASK", "").strip()
    if not task or len(task) > 2500:
        raise RuntimeError("TASK must contain 1–2500 characters")

    allow_sensitive = (
        os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"
    )

    check_clean_tree()

    # --- źródło + kontekst ---
    source_path = ROOT / TARGET_FILE
    if not source_path.exists():
        raise RuntimeError(f"Target file not found: {TARGET_FILE}")
    source = source_path.read_text(encoding="utf-8")

    result = build_context(task, source)
    base_context = result["context"]

    print(
        f"Context selected: {result['chars']} chars "
        f"({len(result['included'])} blocks); "
        f"index.html: {len(source)} chars"
    )
    print(f"Included blocks: {result['included']}")
    print(f"Mode: {'DIAGNOSE_ONLY' if DIAGNOSE_ONLY else 'PATCH'}")

    if result["over_limit"]:
        raise RuntimeError(
            f"Context truncated (limit={MAX_CONTEXT_CHARS}). "
            f"Over limit: {result['over_limit']}. Raise MAX_CONTEXT_CHARS."
        )
    if result["missing"]:
        raise RuntimeError(
            f"Context missing blocks: {result['missing']}. "
            "Fix context_builder.py or the source."
        )

    # --- tryb diagnostyczny: jedno wywołanie, zero patchy, zero QA ---
    if DIAGNOSE_ONLY:
        proposal = propose(task, base_context)
        _print_diagnosis(proposal)
        return

    # --- tryb patch: retry loop z QA ---
    qa_feedback = ""
    last_verdict = None
    last_error = "brak"

    for attempt in range(1, QA_MAX_ATTEMPTS + 1):
        print(f"\n=== Attempt {attempt}/{QA_MAX_ATTEMPTS} ===")

        model_context = base_context
        if qa_feedback:
            model_context = (
                base_context
                + "\n\n// ===== PREVIOUS QA FEEDBACK (fix these specific issues) =====\n"
                + qa_feedback
            )

        proposal = propose(task, model_context)
        status = (proposal or {}).get("status")
        summary = (proposal or {}).get("summary", "")
        print(f"Code agent: {status} — {summary}")

        # --- statusy terminalne: NATYCHMIAST stop ---
        if status in TERMINAL_STATUSES:
            print("\nTerminal status — no retry will help.")
            print(f"Model's reasoning / summary:\n{summary}")
            ops = _extract_operations(proposal)
            if ops:
                print(f"\nBut model did propose {len(ops)} operation(s) before refusing:")
                for i, op in enumerate(ops[:5], 1):
                    if isinstance(op, dict):
                        print(f"  {i}. {str(op.get('find',''))[:80]!r}")
            raise RuntimeError(
                f"Code agent returned terminal status={status!r}. "
                f"Summary: {summary}. "
                "Run with DIAGNOSE_ONLY=true to see full analysis, "
                "then use a narrower task."
            )

        ops = _extract_operations(proposal)
        if not ops:
            last_error = f"no operations (status={status!r})"
            print(f"  → {last_error}; retrying")
            continue

        # --- patch in-memory ---
        try:
            updated = apply_operations(source, proposal)
            enforce_sensitive_guard(source, updated, allow_sensitive)
        except Exception as e:
            last_error = f"patch validation: {e}"
            print(f"  → {last_error}; retrying")
            continue

        # --- diff ---
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

        # --- QA ---
        verdict = review(task, diff)
        last_verdict = verdict
        qa_verdict = verdict.get("verdict")
        print(f"QA verdict: {qa_verdict}")
        for f in verdict.get("findings", [])[:8]:
            print("QA:", f)

        if qa_verdict == "pass":
            print(f"\n✓ QA passed on attempt {attempt}")
            print("AI patch passed static validation and independent review.")
            print("SUMMARY:", summary or "AltCards change")
            return

        if qa_verdict in ("needs_human_review",):
            source_path.write_text(source, encoding="utf-8")
            findings = "\n".join(f"- {x}" for x in verdict.get("findings", []))
            raise RuntimeError(
                "QA returned needs_human_review — this is terminal. "
                "Model or task scope is too vague for automated fix. "
                "Run with DIAGNOSE_ONLY=true to understand, then narrow "
                f"the task. QA findings:\n{findings}"
            )

        # qa_verdict == 'fail' → retry z feedbackiem
        qa_feedback = "\n".join(
            f"- {x}" for x in verdict.get("findings", [])
        ) or "(no detailed findings)"
        source_path.write_text(source, encoding="utf-8")
        last_error = f"QA rejected: {qa_feedback[:300]}"
        print("  → QA rejected; retrying with feedback")

    raise RuntimeError(
        f"All {QA_MAX_ATTEMPTS} QA attempts failed. "
        f"Last verdict: {last_verdict and last_verdict.get('verdict')}. "
        f"Last error: {last_error}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
