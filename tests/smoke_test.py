"""
tests/smoke_test.py — AltCards

Testy regresyjne selekcji kontekstu. Uruchamiaj:
    python tests/smoke_test.py
Kod wyjścia 0 = wszystkie testy przeszły.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.context_builder import build_context, MAX_CONTEXT_CHARS, TARGET_FILE  # noqa: E402


SOURCE = (ROOT / TARGET_FILE).read_text(encoding="utf-8")


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1. Ogólne zadanie XP — dokładnie to, na czym agent się wykładał
# ---------------------------------------------------------------------------
def test_generic_xp_task_is_complete() -> None:
    task = "Sprawdź dlaczego XP nie zwiększa się i czy może być gubione"
    res = build_context(task, SOURCE)
    ctx = res["context"]

    _assert(res["chars"] <= MAX_CONTEXT_CHARS,
            f"kontekst przekracza limit: {res['chars']} > {MAX_CONTEXT_CHARS}")
    _assert(not res["missing"], f"brakują bloki: {res['missing']}")
    _assert(not res["over_limit"], f"pominięto z powodu limitu: {res['over_limit']}")

    required = [
        # pełne bloki
        "function getLevelInfo", "function getRank", "function render",
        "function vStudy", "function vStats",
        "function ans", "function undoAnswer", "function grade",
        # Sync — pełne metody
        "async startSession", "async download", "async logXp",
        "async queueUpload", "async upload", "async checkBonuses",
        # init + save
        "const defaults", "const save", "try{S=JSON.parse",
        # krytyczne kotwice
        "lvl-display",
        "S.settings.xp",
        "get_leaderboard",
        "add_xp",
    ]
    for token in required:
        _assert(token in ctx, f"brak w kontekście XP: {token!r}")

    # ucięte bloki — nie mogą kończyć się w środku (np. przedwczesny `}`)
    _assert("return { level: lvl, currentXp: tempXp, nextLevelXp: req };" in ctx,
            "getLevelInfo wygląda na ucięty — brak pełnego `return`")
    _assert("Sync?.queueUpload" in ctx,
            "save() wygląda na ucięty — brak wywołania queueUpload")


# ---------------------------------------------------------------------------
# 2. Zadanie bonusów rankingowych — musi zawierać logikę bonusu
# ---------------------------------------------------------------------------
def test_ranking_bonus_task() -> None:
    task = "Dlaczego bonus XP za top 3 w rankingu dziennym nie jest widoczny w levelu?"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("checkBonuses", "get_leaderboard", "add_xp",
                  "S.settings.xp", "xp_logs", "serverXp"):
        _assert(token in ctx, f"brak w kontekście bonusu: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


# ---------------------------------------------------------------------------
# 3. Zadanie logowania — nie może regresować poprzedniej poprawki
# ---------------------------------------------------------------------------
def test_login_task() -> None:
    task = "Usuń podtytuł na ekranie logowania i zmień tagline"
    res = build_context(task, SOURCE)
    ctx = res["context"]
    for token in ("function showLogin", "changeLoginLang",
                  "login.tagline", "login.subtitle"):
        _assert(token in ctx, f"brak w kontekście logowania: {token!r}")
    _assert(res["chars"] <= MAX_CONTEXT_CHARS, "przekroczony limit znaków")


# ---------------------------------------------------------------------------
# 4. Weryfikacja brace-matchera na znanym przypadku
# ---------------------------------------------------------------------------
def test_brace_matcher_handles_templates_and_nested() -> None:
    from agent.context_builder import _match_braces
    sample = 'function f(){ const x = `a${ {b:1}.b }c`; if(1){return 2} }'
    start = sample.index("{")
    end = _match_braces(sample, start)
    _assert(end is not None, "matcher zwrócił None dla poprawnego bloku")
    _assert(sample[start:end].endswith("}"),
            "matcher nie domknął na ostatniej klamrze")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def main() -> int:
    tests = [
        test_brace_matcher_handles_templates_and_nested,
        test_generic_xp_task_is_complete,
        test_ranking_bonus_task,
        test_login_task,
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
