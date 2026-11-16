from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "index.html"
AGENT_DIR = ROOT / "agent"
sys.path.insert(0, str(AGENT_DIR))
sys.dont_write_bytecode = True


def load_context_builder_from_source():
    """Load the checked-out .py source directly, without potentially stale .pyc files."""
    path = AGENT_DIR / "context_builder.py"
    module = types.ModuleType("_altcards_context_builder_test")
    module.__file__ = str(path)
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    exec(code, module.__dict__)
    return module.build_context


build_context = load_context_builder_from_source()


class Parser(HTMLParser):
    pass


def main():
    if not HTML.is_file():
        raise SystemExit("index.html not found in repository root")
    src = HTML.read_text(encoding="utf-8")
    if "<html" not in src.lower() or "</html>" not in src.lower():
        raise SystemExit("index.html is not a complete HTML document")

    parser = Parser(convert_charrefs=True)
    parser.feed(src)
    parser.close()

    required = [
        "function grade(", "function ans(", "function undoAnswer(",
        "function start(", "Sync = {", "function vStudy(",
        "function vCards(", "function vLibrary(", "function vStats(",
        "function vSettings(", "function saveSettings(",
    ]
    missing = [item for item in required if item not in src]
    if missing:
        raise SystemExit("Missing expected AltCards symbols: " + ", ".join(missing))

    task = (
        'Na ekranie logowania AltCards usuń kursywę z tekstu '
        '„Alternatywny sposób nauki” i usuń tekst '
        '„Zaloguj się, aby zsynchronizować fiszki”.'
    )
    _, login_context = build_context(task, ROOT)

    checks = {
        "login renderer was not included (showLogin)": "FUNCTION showLogin:" in login_context,
        "login language-switch function was not included": "FUNCTION changeLoginLang:" in login_context,
        "login tagline translation was not included": "Alternatywny sposób nauki." in login_context,
        "login subtitle translation was not included": "Zaloguj się, aby zsynchronizować fiszki." in login_context,
        "protected Sync implementation was incorrectly included": "SYNC OBJECT:" not in login_context,
        "selected context was truncated": "CONTEXT CUT" not in login_context,
    }
    failed = [message for message, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("Login context selection test failed:\n- " + "\n- ".join(failed))

    # Login CSS blocks are supplemental: this task's relevant italic style is
    # inline in showLogin(), so requiring a separate stylesheet match would
    # reject valid source layouts unnecessarily.

    xp_task = (
        "Sprawdź dlaczego XP nie zwiększa się po odpowiedzi na fiszkę; "
        "przeanalizuj naliczanie, zapis i wyświetlanie poziomu."
    )
    _, xp_context = build_context(xp_task, ROOT)
    xp_checks = {
        "XP level calculation missing (getLevelInfo)": "FUNCTION getLevelInfo:" in xp_context,
        "XP rank mapping missing (getRank)": "FUNCTION getRank:" in xp_context,
        "main render dispatcher missing": "FUNCTION render:" in xp_context,
        "study dashboard missing (vStudy / lvl-display)": "FUNCTION vStudy:" in xp_context and "lvl-display" in xp_context,
        "XP award path missing (ans)": "FUNCTION ans:" in xp_context,
        "card grading path missing (grade)": "FUNCTION grade:" in xp_context,
        "undo path that can revert XP missing": "FUNCTION undoAnswer:" in xp_context,
        "local defaults / storage / save context missing": (
            "XP STATE, DEFAULTS AND LOCAL PERSISTENCE:" in xp_context
            and "localStorage.getItem('altcards-v1')" in xp_context
            and "const save=()=>" in xp_context
            and "xp:0" in xp_context
        ),
        "server XP log and state hydration missing": (
            "SYNC METHODS RELEVANT TO XP" in xp_context
            and "xp_logs" in xp_context
            and "serverXp" in xp_context
            and "async download()" in xp_context
        ),
        "entire Sync object should not be sent for an XP investigation": "SYNC OBJECT:" not in xp_context,
        "XP context was truncated": "CONTEXT CUT" not in xp_context,
    }
    xp_failed = [message for message, passed in xp_checks.items() if not passed]
    if xp_failed:
        raise SystemExit("XP context selection test failed:\n- " + "\n- ".join(xp_failed))

    patterns = [
        r"sk-[A-Za-z0-9_-]{20,}",
        r"DEEPSEEK_API_KEY\s*[:=]\s*['\"][^'\"]+",
        r"SUPABASE_SERVICE_ROLE",
    ]
    for pattern in patterns:
        if re.search(pattern, src, re.I):
            raise SystemExit("Potential secret found in index.html")

    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for inline JavaScript syntax validation")
    scripts = re.findall(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", src, re.I | re.S)
    for i, script in enumerate(scripts, 1):
        if not script.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tmp:
            tmp.write(script)
            tmp_path = tmp.name
        try:
            result = subprocess.run([node, "--check", tmp_path], text=True, capture_output=True)
            if result.returncode:
                raise SystemExit(f"Inline script {i} syntax error:\n{result.stderr}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    print(
        f"Static checks passed: HTML parsed, {len(scripts)} inline script(s) syntax-checked; "
        "login and XP context selection passed."
    )


if __name__ == "__main__":
    main()
