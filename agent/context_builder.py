"""
agent/context_builder.py — AltCards

Buduje minimalny, ale KOMPLETNY kontekst dla agenta DeepSeek.
Wycina bloki przez LICZENIE KLAMER (nie `find('}')`) — obsługuje
stringi, template literale `${...}` i komentarze.
"""
from __future__ import annotations

import re

MAX_CONTEXT_CHARS = 24_000
TARGET_FILE = "index.html"
try:
    from .settings import MAX_CONTEXT_CHARS, TARGET_FILE  # type: ignore[no-redef]
except Exception:
    try:
        from settings import MAX_CONTEXT_CHARS, TARGET_FILE  # type: ignore[no-redef]
    except Exception:
        pass

XP_SNIPPET_BUDGET = 24
SECTION_MARKER = re.compile(r"\n//\s*={3,}")


def _match_braces(source: str, brace_start: int) -> int | None:
    if brace_start >= len(source) or source[brace_start] != "{":
        return None
    stack = ["brace"]
    i = brace_start + 1
    n = len(source)
    while i < n:
        c = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = source.find("\n", i)
            i = (j + 1) if j != -1 else n
            continue
        if c == "/" and nxt == "*":
            j = source.find("*/", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        top = stack[-1]
        if top == "template":
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == "$" and nxt == "{":
                stack.append("brace")
                i += 2
                continue
            if c == "`":
                stack.pop()
                i += 1
                continue
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            while i < n:
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "`":
            stack.append("template")
            i += 1
            continue
        if c == "{":
            stack.append("brace")
            i += 1
            continue
        if c == "}":
            stack.pop()
            i += 1
            if not stack:
                return i
            continue
        i += 1
    return None


def _extract_function_decl(source: str, name: str) -> str | None:
    for m in re.finditer(rf"function\s+{re.escape(name)}\s*\(", source):
        brace = source.find("{", m.end())
        if brace == -1:
            continue
        end = _match_braces(source, brace)
        if end is not None:
            return source[m.start():end]
    return None


def _extract_arrow_function(source: str, name: str) -> str | None:
    pat = rf"\bconst\s+{re.escape(name)}\s*=\s*(?:\([^)]*\)|\w+)\s*=>"
    for m in re.finditer(pat, source):
        j = m.end()
        n = len(source)
        while j < n and source[j] in " \t\r\n":
            j += 1
        if j < n and source[j] == "{":
            end = _match_braces(source, j)
            if end is not None:
                k = end
                while k < n and source[k] in " \t":
                    k += 1
                if k < n and source[k] == ";":
                    k += 1
                return source[m.start():k]
        else:
            depth = 0
            k = j
            while k < n:
                ch = source[k]
                if ch in "({[":
                    depth += 1
                elif ch in ")}]":
                    if depth == 0:
                        break
                    depth -= 1
                elif ch == ";" and depth == 0:
                    return source[m.start():k + 1]
                k += 1
    return None


def _extract_const_object(source: str, name: str) -> str | None:
    pat = rf"\bconst\s+{re.escape(name)}\s*=\s*\{{"
    for m in re.finditer(pat, source):
        brace = source.rfind("{", m.start(), m.end())
        if brace == -1:
            continue
        end = _match_braces(source, brace)
        if end is not None:
            k = end
            while k < len(source) and source[k] in " \t":
                k += 1
            if k < len(source) and source[k] == ";":
                k += 1
            return source[m.start():k]
    return None


def extract_named(source: str, name: str) -> str | None:
    return (
        _extract_function_decl(source, name)
        or _extract_arrow_function(source, name)
        or _extract_const_object(source, name)
    )


def _find_sync_object(source: str) -> tuple[int, int] | tuple[None, None]:
    m = re.search(r"\bSync\s*=\s*\{", source)
    if not m:
        return None, None
    brace = m.end() - 1
    end = _match_braces(source, brace)
    if end is None:
        return None, None
    return m.start(), end


def extract_sync_method(source: str, method: str) -> str | None:
    sync_start, sync_end = _find_sync_object(source)
    if sync_start is None:
        return None
    body = source[sync_start:sync_end]
    pat = rf"(?:async\s+)?\b{re.escape(method)}\s*\([^)]*\)\s*\{{"
    m = re.search(pat, body)
    if not m:
        pat2 = rf"(?:async\s+)?\b{re.escape(method)}\s*\([^)]*\)\s*=>"
        m2 = re.search(pat2, body)
        if not m2:
            return None
        brace = body.find("{", m2.end())
        if brace == -1:
            return None
        end = _match_braces(body, brace)
        return body[m2.start():end] if end else None
    brace = body.rfind("{", m.start(), m.end())
    if brace == -1:
        return None
    end = _match_braces(body, brace)
    if end is None:
        return None
    line_start = body.rfind("\n", 0, m.start())
    line_start = 0 if line_start == -1 else line_start + 1
    k = end
    while k < len(body) and body[k] in " \t":
        k += 1
    if k < len(body) and body[k] == ",":
        k += 1
    return body[line_start:k]


def extract_init_block(source: str) -> str:
    start_match = re.search(
        r"try\s*\{\s*S\s*=\s*JSON\.parse\(localStorage\.getItem\(",
        source,
    )
    if not start_match:
        return ""
    start = start_match.start()
    marker = SECTION_MARKER.search(source, start)
    end = marker.start() if marker else min(len(source), start + 4000)
    return source[start:end].rstrip() + "\n"


XP_KEYWORDS = (
    "xp", "level", "poziom", "lvl", "bonus", "ranking", "leaderboard",
    "getlevelinfo", "getrank", "render", "save", "lvl-display",
    "undo", "cofnij",
)
LOGIN_KEYWORDS = (
    "login", "logowania", "zaloguj", "tagline", "subtitle", "showlogin",
    "changelang", "ekran logowania",
)


def _is_xp_task(task_low: str) -> bool:
    return any(k in task_low for k in XP_KEYWORDS)


def _is_login_task(task_low: str) -> bool:
    return any(k in task_low for k in LOGIN_KEYWORDS)


def build_context(task: str, source: str) -> dict:
    task_low = task.lower()
    is_xp = _is_xp_task(task_low)
    is_login = _is_login_task(task_low)

    ordered: list[tuple[str, str]] = []

    def push(label: str, body: str | None) -> None:
        if body:
            ordered.append((label, body))

    push("init_block", extract_init_block(source))
    push("defaults", extract_named(source, "defaults"))
    push("save", extract_named(source, "save"))
    push("getLevelInfo", extract_named(source, "getLevelInfo"))
    push("getRank", extract_named(source, "getRank"))
    push("render", extract_named(source, "render"))

    if is_xp:
        for name in ("vStudy", "vStats", "ans", "undoAnswer", "grade"):
            push(name, extract_named(source, name))
        for method in (
            "init", "startSession", "download", "logXp",
            "queueUpload", "upload", "checkBonuses",
        ):
            push(f"Sync.{method}", extract_sync_method(source, method))

    if is_login:
        push("showLogin", extract_named(source, "showLogin"))
        push("changeLoginLang", extract_named(source, "changeLoginLang"))
        for k in ("login.tagline", "login.subtitle", "login.title"):
            m = re.search(
                rf"['\"]{re.escape(k)}['\"]\s*:\s*\{{[^}}]*\}}",
                source,
            )
            if m:
                push(f"TR[{k}]", m.group(0))

    parts: list[str] = []
    included: list[str] = []
    over_limit: list[str] = []
    seen_bodies: set[str] = set()
    budget = MAX_CONTEXT_CHARS
    used = 0
    snippet_cap = XP_SNIPPET_BUDGET if is_xp else 12

    for label, body in ordered:
        key = body.strip()
        if key in seen_bodies:
            continue
        if len(included) >= snippet_cap:
            over_limit.append(label)
            continue
        chunk = f"\n// ===== {label} =====\n{key}\n"
        if used + len(chunk) > budget:
            over_limit.append(label)
            continue
        parts.append(chunk)
        included.append(label)
        seen_bodies.add(key)
        used += len(chunk)

    ctx = "".join(parts)
    missing: list[str] = []
    for label, _ in ordered:
        if label not in included and label not in over_limit:
            missing.append(label)

    if is_xp:
        for token in (
            "lvl-display", "S.settings.xp", "getLevelInfo",
            "logXp", "checkBonuses",
        ):
            if token not in ctx:
                missing.append(f"brak_token:{token}")

    ok = not missing and not over_limit

    return {
        "context": ctx.strip(),
        "included": included,
        "missing": missing,
        "over_limit": over_limit,
        "chars": len(ctx),
        "ok": ok,
    }
