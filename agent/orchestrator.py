"""
agent/orchestrator.py — AltCards

Pipeline:
  1. clean tree check,
  2. kontekst XP z context_builder,
  3. Code Agent → operations / analiza,
  4. patch_validator,
  5. QA review z retry loop.
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
QA_MAX_ATTEMPTS = int(os.getenv("QA_MAX_ATTEMPTS", "3"))
DIAGNOSE_ONLY = os.getenv("DIAGNOSE_ONLY", "false").lower() == "true"

# Tylko te statusy naprawdę kończą pipeline.
TRULY_TERMINAL = {"need_context", "refused"}

RETRY_NUDGE = (
    "\n\n// ===== IMPORTANT INSTRUCTIONS FOR THIS RETRY =====\n"
    "You MUST produce at least one find/replace operation.\n"
    "Returning `cannot_do_safely`, `needs_human_review`, or empty operations "
    "is NOT acceptable when QA has given specific fixable feedback.\n"
    "Read each QA finding below and address it with a concrete code change.\n"
    "Stay inside the target task scope: no refactors, no unrelated changes.\n"
)


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
            "Checkout is not clean before agent run. Unexpected entries:\n"
            + details
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
    if not task or len(task) > 2500:
        raise RuntimeError("TASK must contain 1–2500 characters")

    allow_sensitive = (
        os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"
    )

    check_clean_tree()

    source_path = ROOT / TARGET_FILE
    if not source_path.exists():
        raise RuntimeError(f"Target file not found: {TARGET_FILE}")
    source = source_path.read_text(encoding="utf-8")

    result = build_context(task, source)
    base_context = result["context"]

    print(
        f"Context selected: {result['chars']} chars "
        f"({len(result['included'])} blocks); index.html: {len(source)} chars"
    )
    print(f"Mode: {'DIAGNOSE_ONLY' if DIAGNOSE_ONLY else 'PATCH'}")

    if result["over_limit"]:
        raise RuntimeError(
            f"Context truncated (limit={MAX_CONTEXT_CHARS}). "
            f"Over limit: {result['over_limit']}. Raise MAX_CONTEXT_CHARS."
        )
    if result["missing"]:
        raise RuntimeError(
            f"Context missing blocks: {result['missing']}. "
            "Fix context_builder.py."
        )

    if DIAGNOSE_ONLY:
        _print_diagnosis(propose(task, base_context))
        return

    qa_feedback = ""
    last_verdict = None
    last_error = "brak"

    for attempt in range(1, QA_MAX_ATTEMPTS + 1):
        print(f"\n=== Attempt {attempt}/{QA_MAX_ATTEMPTS} ===")

        model_context = base_context
        if qa_feedback:
            model_context = base_context + "\n\n" + qa_feedback + RETRY_NUDGE

        proposal = propose(task, model_context)
        status = (proposal or {}).get("status")
        summary = (proposal or {}).get("summary", "")
        print(f"Code agent: {status} — {summary}")

        if status in TRULY_TERMINAL:
            raise RuntimeError(
                f"Code agent returned terminal status={status!r}. "
                f"Summary: {summary}. "
                "Use DIAGNOSE_ONLY=true to inspect, then narrow the task."
            )

        # cannot_do_safely / needs_human_review / brak operacji → retry z nudge
        ops = _extract_operations(proposal)
        if not ops:
            last_error = f"no operations (status={status!r})"
            print(f"  → {last_error}; retrying with stronger nudge")
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
            last_error = "no diff"
            print(f"  → {last_error}; retrying")
            continue

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

        # wszystkie inne QA (fail, needs_human_review) → retry z feedbackiem
        qa_feedback = (
            "// ===== PREVIOUS QA FINDINGS — ADDRESS EACH ONE =====\n"
            + "\n".join(f"- {x}" for x in verdict.get("findings", []))
        )
        source_path.write_text(source, encoding="utf-8")
        last_error = f"QA {qa_verdict}"
        print(f"  → QA {qa_verdict}; retrying with feedback")

    raise RuntimeError(
        f"All {QA_MAX_ATTEMPTS} attempts failed. "
        f"Last verdict: {last_verdict and last_verdict.get('verdict')}. "
        f"Last error: {last_error}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
