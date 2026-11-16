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
    "xp": ["__XP_CONTEXT__"],
    "poziom": ["__XP_CONTEXT__"],
    "level": ["__XP_CONTEXT__"],
    "punkty doświadczenia": ["__XP_CONTEXT__"],
    "undo": ["undoAnswer", "ans"],
    "cofn": ["undoAnswer", "ans"],
    "sync": ["Sync", "save"],
    "synchron": ["Sync", "save"],
    "supabase": ["Sync"],
    # Login UI is rendered dynamically in showLogin(); its visible copy lives
    # in the TR dictionary, not as literal HTML text.
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


def _extract_sync_method(source, name):
    """Extract one top-level method from the Sync object, not the whole object."""
    start_match = re.search(
        rf"(?m)^  (?:async\s+)?{re.escape(name)}\s*\([^\n]*\)\s*\{{", source
    )
    if not start_match:
        return None
    next_match = re.search(
        r"(?m)^  (?:async\s+)?[A-Za-z_$][\w$]*\s*\([^\n]*\)\s*\{", 
        source[start_match.end():]
    )
    end = start_match.end() + next_match.start() if next_match else len(source)
    return source[start_match.start():end].strip().rstrip(",")


def _extract_regex(source, pattern):
    m = re.search(pattern, source)
    return m.group(0).strip() if m else None


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


def _xp_context(source, task_low):
    """Return the whole XP path: award, undo, dashboard, persistence and sync hydration."""
    blocks = []
    for name in ("getLevelInfo", "getRank", "render", "vStudy", "grade", "ans", "undoAnswer"):
        block = _extract_function(source, name)
        if block:
            blocks.append(f"FUNCTION {name}:\n{block}")

    state_parts = [
        _extract_regex(source, r"(?m)^let Sync=null, S=null.*$"),
        _extract_regex(source, r"(?m)^const defaults=.*?;"),
    ]
    init_start = source.find("try{S=JSON.parse(localStorage.getItem('altcards-v1'))}catch(e){}")
    init_end = source.find("const isNew=", init_start) if init_start >= 0 else -1
    if init_start >= 0 and init_end > init_start:
        state_parts.append(source[init_start:init_end].strip())
    save_start = source.find("const save=()=>{")
    save_end = source.find("};", save_start) if save_start >= 0 else -1
    if save_start >= 0 and save_end > save_start:
        state_parts.append(source[save_start:save_end + 2].strip())
    state_text = "\n".join(x for x in state_parts if x)
    if state_text:
        blocks.append("XP STATE, DEFAULTS AND LOCAL PERSISTENCE:\n" + state_text)

    sync_methods = []
    for name in ("startSession", "download", "logXp", "queueUpload", "upload"):
        block = _extract_sync_method(source, name)
        if block:
            sync_methods.append(block)
    if any(term in task_low for term in ("bonus", "ranking", "leaderboard", "bonusowy")):
        block = _extract_sync_method(source, "checkBonuses")
        if block:
            sync_methods.append(block)
    if sync_methods:
        blocks.append("SYNC METHODS RELEVANT TO XP (only selected methods):\n" + "\n\n".join(sync_methods))

    return blocks


def build_context(task: str, repo_root: Path):
    path = repo_root / TARGET_FILE
    if not path.is_file():
        raise RuntimeError("index.html not found in checked-out main branch")
    source = path.read_text(encoding="utf-8")
    if len(source) < 5000 or "<html" not in source.lower():
        raise RuntimeError("index.html does not match the expected AltCards application")

    task_low = task.lower()
    xp_task = any(term in task_low for term in (
        "xp", "poziom", "level", "punkty doświadczenia", "punkty doswiadczenia"
    ))
    login_ui_task = any(term in task_low for term in ("login", "logowan", "loguj", "ekran startow"))
    visual_task = any(word in task_low for word in (
        "css", "style", "wyglad", "mobile", "mobil", "spacing",
        "layout", "respons", "odstep", "odstęp", "uklad", "układ"
    ))

    blocks = []
    special_context = False

    # XP must be treated as one connected feature path. The generic alias list
    # previously selected the entire Sync object and several unrelated card
    # functions, then MAX_SNIPPETS truncated the XP UI and persistence context.
    if xp_task:
        blocks.extend(_xp_context(source, task_low))
        special_context = True

    if login_ui_task and not xp_task:
        for name in ("showLogin", "changeLoginLang"):
            block = _extract_function(source, name)
            if block:
                blocks.append(f"FUNCTION {name}:\n{block}")
        tr_start = source.find("'login.title':")
        tr_end = source.find("'nick.title':", tr_start + 1) if tr_start >= 0 else -1
        if tr_start >= 0 and tr_end > tr_start:
            blocks.append("LOGIN TRANSLATIONS (inside const TR):\n" + source[tr_start:tr_end].strip())
        login_css = re.compile(r"(?ms)^[^{}\n]*(?:#login|\.pass-wrap|\.pass-toggle)[^{}\n]*\{[^{}]*\}")
        css_rules = login_css.findall(source)
        if css_rules:
            blocks.append("LOGIN CSS RULES:\n" + "\n".join(rule.strip() for rule in css_rules))
        special_context = True

    if not special_context:
        symbols = []
        for alias, mapped in ALIASES.items():
            if alias in task_low:
                if visual_task and alias in {"study", "nauka", "settings", "setting", "ustawien"}:
                    symbols.extend(["vStudy"] if ("study" in task_low or "nauka" in task_low) else mapped)
                elif visual_task and alias in {"mobile", "mobil", "css", "style", "wyglad", "respons"}:
                    symbols.append("__CSS__")
                else:
                    symbols.extend(mapped)
        declared = {m.group(1) for m in FUNCTION_RE.finditer(source)}
        symbols.extend(name for name in declared if name.lower() in task_low)
        explicit_sync_logic = any(term in task_low for term in (
            "supabase", "sync object", "mechanizm synchron", "logika synchron",
            "synchronizacja danych", "sync logic", "sync implementation",
            "upload", "download", "startsession"
        ))
        if login_ui_task and not explicit_sync_logic:
            symbols = [name for name in symbols if name not in {"Sync", "save"}]
        symbols = list(dict.fromkeys(symbols))

        if visual_task:
            blocks.extend(_css_context(source, task))
        for symbol in symbols:
            if symbol == "__CSS__":
                continue
            elif symbol == "__TR__":
                lines = source.splitlines()
                terms = [w for w in re.findall(r"[^\W_][\w-]{3,}", task_low, re.UNICODE)]
                selected = [(i, line) for i, line in enumerate(lines) if any(t in line.lower() for t in terms)]
                blocks.extend(f"TR line {i+1}:\n{line}" for i, line in selected[:20])
            elif symbol == "Sync":
                block = _extract_anchor(source, "Sync = {", "\n};\nif(IS_DEV)")
                if block:
                    blocks.append("SYNC OBJECT:\n" + block)
            elif symbol == "defaults":
                block = _extract_regex(source, r"(?m)^const defaults\s*=.*?;")
                if block:
                    blocks.append("DEFAULT SETTINGS:\n" + block)
            elif symbol == "save":
                block = _extract_regex(source, r"(?m)^const save\s*=.*?;")
                if block:
                    blocks.append("SAVE FUNCTION:\n" + block)
            else:
                block = _extract_function(source, symbol)
                if block:
                    blocks.append(f"FUNCTION {symbol}:\n{block}")

    if not blocks:
        raise RuntimeError(
            "Could not identify a safe code area. Add the relevant screen, function name, CSS class or subsystem to the task."
        )

    unique = []
    for block in blocks:
        if block and block not in unique:
            unique.append(block)
    docs = []
    for name in ("PROJECT_CONTEXT.md", "ARCHITECTURE.md", "RULES.md"):
        doc = repo_root / "docs" / name
        if doc.exists():
            limit = 500 if xp_task else 1200
            docs.append(f"--- {name} ---\n{doc.read_text(encoding='utf-8')[:limit]}")

    # For XP and login paths, keep every selected block; do not silently drop
    # later pieces of the feature path because a generic snippet count is hit.
    selected_blocks = unique if (xp_task or login_ui_task) else unique[:MAX_SNIPPETS]
    context = "\n\n".join(docs + selected_blocks)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n[CONTEXT CUT: ask for narrower task if missing code]"
    return source, context


def _extract_anchor(source, start_text, end_text):
    start = source.find(start_text)
    if start < 0:
        return None
    end = source.find(end_text, start + len(start_text))
    if end < 0:
        return None
    return source[start:end + len(end_text)].strip()
