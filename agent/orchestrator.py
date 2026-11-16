"""
agent/orchestrator.py — AltCards

Spina cały pipeline:
  1. sprawdza czystość drzewa roboczego (bez __pycache__/*.pyc),
  2. buduje kontekst przez agent/context_builder.build_context(task, source),
  3. wywołuje Code Agenta (DeepSeek) i dostaje operacje find/replace,
  4. waliduje i aplikuje patch przez patch_validator,
  5. pilnuje allowlisty plików i ochrony wrażliwych obszarów,
  6. robi niezależny review QA,
  7. dopiero wtedy workflow może utworzyć Pull Request.

WAŻNE: ten plik współpracuje z context_builder.py, który:
  * przyjmuje (task: str, source: str),
  * zwraca dict: context, included, missing, over_limit, chars, ok.
Nie mieszaj tego z poprzednim tuple API.
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


def run(args):
    return subprocess.run(
        args, cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout


def check_clean_tree():
    """
    Artefakty bytecode Pythona (__pycache__/*.pyc) powstają przy imporcie
    modułów przez samego orkiestratora — nie są zmianami użytkownika,
    więc je ignorujemy. Każda inna brudna pozycja = twardy błąd.
    """
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


def main():
    task = os.getenv("TASK", "").strip()
    if not task or len(task) > 2500:
        raise RuntimeError("TASK must contain 1–2500 characters")

    allow_sensitive = (
        os.getenv("ALLOW_SENSITIVE_CHANGES", "false").lower() == "true"
    )

    check_clean_tree()

    # --- 1. źródło ---
    source_path = ROOT / TARGET_FILE
    if not source_path.exists():
        raise RuntimeError(f"Target file not found: {TARGET_FILE}")
    source = source_path.read_text(encoding="utf-8")

    # --- 2. kontekst ---
    result = build_context(task, source)
    context = result["context"]
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
            "Refine the task scope or raise MAX_CONTEXT_CHARS — "
            "do NOT proceed with a truncated context."
        )
    if result["missing"]:
        raise RuntimeError(
            "Context is missing required blocks: "
            f"{result['missing']}. "
            "Either the source no longer contains them (refactor) or "
            "agent/context_builder.py cannot find them — inspect before running."
        )

    # --- 3. Code Agent ---
    proposal = propose(task, context)
    print(
        f"Code agent: {proposal.get('status')} — "
        f"{proposal.get('summary', '')}"
    )
    if proposal.get("status") == "need_context":
        raise RuntimeError(
            "Code agent still requested more context after builder pass. "
            "This means context_builder.py does not extract everything the "
            "task needs. Summary: "
            f"{proposal.get('summary', '')}"
        )
    if proposal.get("status") not in (None, "ok", "applied", "patch"):
        # każdy inny status (np. error, refused) blokuje dalsze kroki
        raise RuntimeError(
            f"Code agent returned status={proposal.get('status')!r}. "
            f"Summary: {proposal.get('summary', '')}"
        )

    # --- 4. patch ---
    updated = apply_operations(source, proposal)

    enforce_sensitive_guard(source, updated, allow_sensitive)
    source_path.write_text(updated, encoding="utf-8")

    changed = set(run(["git", "diff", "--name-only"]).splitlines())
    enforce_file_allowlist(changed)

    diff = run(["git", "diff", "--", TARGET_FILE])
    if not diff.strip():
        raise RuntimeError(
            "No diff produced — patch had no effect on the target file."
        )

    # --- 5. QA ---
    verdict = review(task, diff)
    print(f"QA verdict: {verdict.get('verdict')}")
    for finding in verdict.get("findings", [])[:8]:
        print("QA:", finding)
    if verdict.get("verdict") != "pass":
        raise RuntimeError(
            "QA did not pass; no Pull Request will be created."
        )

    print("AI patch passed static validation and independent review.")
    print("SUMMARY:", proposal.get("summary", "AltCards change"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
