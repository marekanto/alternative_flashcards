from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "index.html"


class Parser(HTMLParser):
    pass


def main():
    src = HTML.read_text(encoding="utf-8")
    if "<html" not in src.lower() or "</html>" not in src.lower():
        raise SystemExit("index.html is not a complete HTML document")
    parser = Parser(convert_charrefs=True)
    parser.feed(src)
    parser.close()

    # Ensure expected AltCards core symbols still exist.
    required = [
        "function grade(", "function ans(", "function undoAnswer(",
        "function start(", "Sync = {", "function vStudy(",
        "function vCards(", "function vLibrary(", "function vStats(",
        "function vSettings(", "function saveSettings(",
    ]
    missing = [item for item in required if item not in src]
    if missing:
        raise SystemExit("Missing expected AltCards symbols: " + ", ".join(missing))

    # Catch obvious credential accidents in the app source.
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
    print(f"Static checks passed: HTML parsed, {len(scripts)} inline script(s) syntax-checked.")


if __name__ == "__main__":
    main()
