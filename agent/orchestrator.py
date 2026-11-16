"""
agent/orchestrator.py — AltCards

Pipeline:
  1. clean tree check,
  2. kontekst z context_builder.build_context(task, source),
  3. Code Agent → operations,
  4. patch_validator: aplikacja + wrażliwe strefy,
  5. QA review.

Retry loop:
  Gdy QA odrzuci patch, budujemy ponownie ten sam bazowy kontekst
  i doklejamy findings z QA jako sekcję „PREVIOUS QA FAILURES".
  Model dostaje konkretne uwagi i drugą szansę. Maks. QA_MAX_ATTEMPTS prób.

Czego NIE retry-ujemy:
  * need_context — builder nie dostarczył potrzebnych bloków; retry nic nie da,
  * refused — model odmówił wykonania zadania,
  * smoke tests — testują builder (Python), nie patch; naprawia się je raz.

Smoke testy powinny być uruchamiane PRZED tym skryptem w workflow.
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


def main():
    task = os.getenv("TASK", "").strip()
    if not task or len(task) > 2500:
        raise RuntimeError("TASK must contain 1–2500 characters")

    allow_sensitive = (
        os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"
    )

    check_clean_tree()

    # --- źródło + kontekst (raz, na cały loop) ---
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

    if result["over_limit"]:
        raise RuntimeError(
            "Context truncated by char limit "
            f"(limit={MAX_CONTEXT_CHARS} chars). "
            f"Over limit: {result['over_limit']}. "
            "Raise MAX_CONTEXT_CHARS or refine task scope."
        )
    if result["missing"]:
        raise RuntimeError(
            "Context is missing required blocks: "
            f"{result['missing']}. "
            "Fix context_builder.py or the source before running."
        )

    # --- pętla prób ---
    qa_feedback = ""
    last_verdict = None
    last_error = None

    for attempt in range(1, QA_MAX_ATTEMPTS + 1):
        print(f"\n=== Attempt {attempt}/{QA_MAX_ATTEMPTS} ===")

        model_context = base_context
        if qa_feedback:
            model_context = (
                base_context
                + "\n\n// ===== PREVIOUS QA FAILURES (must be addressed) =====\n"
                + qa_feedback
            )

        proposal = propose(task, model_context)
        status = (proposal or {}).get("status")
        summary = (proposal or {}).get("summary", "")
        print(f"Code agent: {status} — {summary}")

        # statusy, które natychmiast przerywają cały pipeline
        if status == "need_context":
            raise RuntimeError(
                "need_context after builder pass. "
                f"context_builder.py does not extract what this task needs. "
                f"Summary: {summary}"
            )
        if status == "refused":
            raise RuntimeError(
                f"Code agent refused: {summary}"
            )

        ops = _extract_operations(proposal)
        if not ops:
            last_error = f"no operations (status={status!r})"
            print(f"  → {last_error}; retrying")
            continue

        # patch in-memory
        try:
            updated = apply_operations(source, proposal)
            enforce_sensitive_guard(source, updated, allow_sensitive)
        except Exception as e:
            last_error = f"patch validation: {e}"
            print(f"  → {last_error}; retrying")
            continue

        # zapis + diff (potrzebne dla review)
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

        # QA
        verdict = review(task, diff)
        last_verdict = verdict
        print(f"QA verdict: {verdict.get('verdict')}")
        for f in verdict.get("findings", [])[:8]:
            print("QA:", f)

        if verdict.get("verdict") == "pass":
            print(f"\n✓ QA passed on attempt {attempt}")
            print("AI patch passed static validation and independent review.")
            print("SUMMARY:", summary or "AltCards change")
            return

        # QA odrzucił — feedback do następnej próby
        qa_feedback = "\n".join(
            f"- {x}" for x in verdict.get("findings", [])
        ) or "(no detailed findings)"
        source_path.write_text(source, encoding="utf-8")  # restore
        last_error = f"QA rejected: {qa_feedback[:300]}"
        print(f"  → QA rejected; retrying with feedback")

    # wszystkie próby wyczerpane
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
