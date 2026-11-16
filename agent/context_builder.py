"""Build compact, task-specific context from AltCards' real index.html."""
from pathlib import Path
import re

from settings import MAX_CONTEXT_CHARS, MAX_SNIPPETS, TARGET_FILE

# Map user language to actual symbols found in the supplied AltCards source.
ALIASES = {
    "study": ["vStudy", "start", "ans", "grade", "undoAnswer"],
    "nauka": ["vStudy", "start", "ans", "grade", "undoAnswer"],
    "powtork": ["vStudy", "start", "grade", "ans"],
    "srs": ["start", "grade", "ans", "undoAnswer"],
    "xp": ["getLevelInfo", "getRank", "ans", "Sync"],
    "undo": ["undoAnswer", "ans"],
    "cofn": ["undoAnswer", "ans"],
    "sync": ["Sync", "save"],
    "synchron": ["Sync", "save"],
    "supabase": ["Sync"],
    # Login UI is rendered dynamically in showLogin(); its visible copy lives
    # in the TR dictionary, not as literal HTML text. Keep auth/sync logic out
    # of ordinary visual login tasks unless the task explicitly asks for it.
    "login": ["__LOGIN__"],
    "logowan": ["__LOGIN__"],
    "loguj": ["__LOGIN__"],
    "ekran startow": ["__LOGIN__"],
    "auth": ["Sync"],
    "deck": ["vCards", "addDeck", "delDeck", "dlist"],
    "talie": ["vCards", "addDeck", "delDeck", "dlist"],
    "card": ["renderCards", "editCard", "delCard", "addCard"],
    "fiszk": ["vStudy", "renderCards", "editCard", "addCard"],
    "csv": ["parseCSV", "importCSV"],
    "import": ["parseCSV", "importCSV"],
    "bibliotek": ["vLibrary", "renderLibGroups", "copyDeckAt", "Sync"],
    "library": ["vLibrary", "renderLibGroups", "copyDeckAt", "Sync"],
    "stats": ["vStats", "computeStreak"],
    "statyst": ["vStats", "computeStreak"],
    "setting": ["vSettings", "saveSettings"],
    "ustawien": ["vSettings", "saveSettings"],
    "limit": ["vStudy", "start", "ans", "saveSettings", "defaults"],
    "feedback": ["openFbx", "closeFbx", "submitFbx"],
    "opini": ["openFbx", "closeFbx", "submitFbx"],
    "css": ["__CSS__"],
    "style": ["__CSS__"],
    "wyglad": ["__CSS__"],
    "respons": ["__CSS__"],
    "mobile": ["__CSS__"],
    "mobil": ["__CSS__"],
    "tlumac": ["__TR__"],
    "translation": ["__TR__"],
    "jezyk": ["__TR__"],
}

FUNCTION_RE = re.compile(r"(?m)^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")


def _extract_function(source, name):
    matches = [m for m in FUNCTION_RE.finditer(source) if m.group(1) == name]
    if len(matches) != 1:
        return None
    start = matches[0].start()
    next_match = FUNCTION_RE.search(source, matches[0].end())
    end = next_match.start() if next_match else len(source)
    return source[start:end].strip()


def _extract_anchor(source, start_text, end_text):
    start = source.find(start_text)
    if start < 0:
        return None
    end = source.find(end_text, start + len(start_text))
    if end < 0:
        return None
    return source[start:end + len(end_text)].strip()


def _css_context(source, task):
    lines = source.splitlines()
    terms = [x for x in re.findall(r"[^\W_][\w-]{2,}", task.lower(), re.UNICODE)
             if x not in {"popraw", "zmien", "zrob", "please", "improve", "change", "fix", "css", "style"}]
    hits = []
    for i, line in enumerate(lines):
        if "{" not in line or "}" in line:
            continue
        low = line.lower()
        score = sum(1 for term in terms if term in low)
        if score:
            hits.append((score, i))
    hits.sort(key=lambda x: (-x[0], x[1]))
    # Generic visual tasks often describe appearance rather than literal
    # selector names. Include common layout controls and media-query rules
    # as a bounded fallback instead of returning no CSS context.
    if not hits:
        preferred = ("body", "button", ".row", ".p", ".s", ".c", "nav", "@media")
        hits = [(1, i) for i, line in enumerate(lines)
                if any(token in line.strip().lower() for token in preferred)]
    windows, out = [], []
    for score, i in hits:
        a, b = max(0, i - 3), min(len(lines), i + 18)
        if any(not (b < x or a > y) for x, y in windows):
            continue
        windows.append((a, b))
        out.append(f"CSS lines {a+1}-{b}:\n" + "\n".join(lines[a:b]))
        if len(out) >= 3:
            break
    return out


def build_context(task: str, repo_root: Path):
    path = repo_root / TARGET_FILE
    if not path.is_file():
        raise RuntimeError("index.html not found in checked-out main branch")
    source = path.read_text(encoding="utf-8")
    if len(source) < 5000 or "<html" not in source.lower():
        raise RuntimeError("index.html does not match the expected AltCards application")

    task_low = task.lower()
    visual_task = any(word in task_low for word in (
        "css", "style", "wyglad", "mobile", "mobil", "spacing",
        "layout", "respons", "odstep", "odstęp", "uklad", "układ"
    ))
    symbols = []
    for alias, mapped in ALIASES.items():
        if alias in task_low:
            if visual_task and alias in {"study", "nauka", "settings", "setting", "ustawien"}:
                # Visual changes to the study dashboard belong to vStudy,
                # not to the SRS answer/queue functions or settings persistence.
                symbols.extend(["vStudy"] if ("study" in task_low or "nauka" in task_low) else mapped)
            elif visual_task and alias in {"mobile", "mobil", "css", "style", "wyglad", "respons"}:
                symbols.append("__CSS__")
            else:
                symbols.extend(mapped)
    # Explicit function names in task are also honored.
    declared = {m.group(1) for m in FUNCTION_RE.finditer(source)}
    symbols.extend(name for name in declared if name.lower() in task_low)
    # Words such as "synchronizować" can be part of the login subtitle itself.
    # For a login-screen task, do not pull in the full protected Sync object
    # unless the user clearly asks to change synchronization/auth behavior.
    login_ui_task = "__LOGIN__" in symbols
    explicit_sync_logic = any(term in task_low for term in (
        "supabase", "sync object", "mechanizm synchron", "logika synchron",
        "synchronizacja danych", "synchronizacjami danych", "sync logic",
        "sync implementation", "upload", "download", "startsession"
    ))
    if login_ui_task and not explicit_sync_logic:
        symbols = [name for name in symbols if name not in {"Sync", "save"}]

    symbols = list(dict.fromkeys(symbols))

    # Put visual context first so it is not crowded out by unrelated JS functions.
    css_blocks = _css_context(source, task) if visual_task else []
    blocks = list(css_blocks)
    for symbol in symbols:
        if symbol == "__CSS__":
            continue
        elif symbol == "__TR__":
            # Translation object is large; include only its declaration start and relevant translation keys.
            lines = source.splitlines()
            terms = [w for w in re.findall(r"[^\W_][\w-]{3,}", task_low, re.UNICODE)]
            selected = [(i, line) for i, line in enumerate(lines) if any(t in line.lower() for t in terms)]
            blocks.extend(f"TR line {i+1}: {line}" for i, line in selected[:20])
        elif symbol == "__LOGIN__":
            # The login form is built at runtime. Include the renderer, the
            # language-switch function, the exact login translation entries,
            # and the CSS selectors that actually style this screen.
            for name in ("showLogin", "changeLoginLang"):
                block = _extract_function(source, name)
                if block:
                    blocks.append(f"FUNCTION {name}:\n{block}")

            tr_start = source.find("'login.title':")
            tr_end = source.find("'nick.title':", tr_start + 1) if tr_start >= 0 else -1
            if tr_start >= 0 and tr_end > tr_start:
                blocks.append("LOGIN TRANSLATIONS (inside const TR):\n" + source[tr_start:tr_end].strip())

            login_css = re.compile(
                r"(?ms)^[^{}\n]*(?:#login|\.pass-wrap|\.pass-toggle)[^{}\n]*\{[^{}]*\}"
            )
            css_rules = login_css.findall(source)
            if css_rules:
                blocks.append("LOGIN CSS RULES:\n" + "\n".join(rule.strip() for rule in css_rules))
        elif symbol == "Sync":
            block = _extract_anchor(source, "Sync = {", "\n};\nif(IS_DEV)")
            if block:
                blocks.append("SYNC OBJECT:\n" + block)
        elif symbol == "defaults":
            m = re.search(r"(?m)^const defaults\s*=.*?;", source)
            if m:
                blocks.append("DEFAULT SETTINGS:\n" + m.group(0))
        elif symbol == "save":
            m = re.search(r"(?m)^const save\s*=.*?;", source)
            if m:
                blocks.append("SAVE FUNCTION:\n" + m.group(0))
        else:
            block = _extract_function(source, symbol)
            if block:
                blocks.append(f"FUNCTION {symbol}:\n{block}")

    # Always add concise state/translation/theme context only if relevant.
    if any(x in task_low for x in ("limit", "srs", "xp", "sync", "synchron", "dzien", "daily")):
        for pattern, label in [
            (r"(?m)^let Sync=null, S=null.*$", "GLOBAL STATE"),
            (r"(?m)^const defaults=.*?;$", "DEFAULT SETTINGS"),
            (r"(?m)^const save=.*?;$", "PERSISTENCE"),
        ]:
            m = re.search(pattern, source)
            if m:
                blocks.append(label + ":\n" + m.group(0))

    # If no semantic mapping worked, fail closed rather than send arbitrary code.
    if not blocks:
        raise RuntimeError(
            "Could not identify a safe code area. Add the relevant screen, function name, CSS class or subsystem to the task."
        )

    # De-duplicate exact blocks and cap count/size.
    unique = []
    for block in blocks:
        if block not in unique:
            unique.append(block)
    docs = []
    for name in ("PROJECT_CONTEXT.md", "ARCHITECTURE.md", "RULES.md"):
        doc = repo_root / "docs" / name
        if doc.exists():
            docs.append(f"--- {name} ---\n{doc.read_text(encoding='utf-8')[:1200]}")
    context = "\n\n".join(docs + unique[:MAX_SNIPPETS])
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n[CONTEXT CUT: ask for narrower task if missing code]"
    return source, context
