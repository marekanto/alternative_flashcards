import re
from settings import TARGET_FILE, ALLOWED_FILES

FUNCTION_RE = re.compile(r"(?m)^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
PROTECTED_FUNCTIONS = {"start", "grade", "ans", "undoAnswer", "saveSettings"}
PROTECTED_ANCHORS = {
    "sync": ("Sync = {", "\n};\nif(IS_DEV)"),
    "defaults": ("const defaults=", ";"),
    "save": ("const save=", ";"),
    "state": ("let Sync=null, S=null", "\n"),
}


def apply_operations(source, result):
    if result.get("status") != "ready":
        raise RuntimeError(f"Agent stopped safely: {result.get('status')} — {result.get('reason', '')}")
    operations = result.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 5:
        raise RuntimeError("Expected 1–5 exact search/replace operations")
    updated = source
    for i, op in enumerate(operations, 1):
        if not isinstance(op, dict):
            raise RuntimeError(f"Patch operation {i} is not an object")
        find, replace = op.get("find"), op.get("replace")
        if not isinstance(find, str) or not find.strip() or not isinstance(replace, str):
            raise RuntimeError(f"Patch operation {i} has invalid find/replace values")
        if len(find) > 6000 or len(replace) > 8000:
            raise RuntimeError(f"Patch operation {i} is too large; request a smaller change")
        count = updated.count(find)
        if count != 1:
            raise RuntimeError(f"Patch operation {i} rejected: find text occurs {count} times (must be exactly once)")
        updated = updated.replace(find, replace, 1)
    if updated == source:
        raise RuntimeError("Patch produced no changes")
    return updated


def _top_level_function_blocks(source):
    matches = list(FUNCTION_RE.finditer(source))
    out = {}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        out[match.group(1)] = source[match.start():end]
    return out


def _anchor(source, start, end_marker):
    pos = source.find(start)
    if pos < 0:
        return None
    end = source.find(end_marker, pos + len(start))
    return source[pos:end + len(end_marker)] if end >= 0 else None


def changed_protected_areas(before, after):
    oldf, newf = _top_level_function_blocks(before), _top_level_function_blocks(after)
    changed = [name for name in PROTECTED_FUNCTIONS if oldf.get(name) != newf.get(name)]
    for name, (start, end) in PROTECTED_ANCHORS.items():
        if _anchor(before, start, end) != _anchor(after, start, end):
            changed.append(name)
    return sorted(set(changed))


def enforce_sensitive_guard(before, after, allow_sensitive):
    changed = changed_protected_areas(before, after)
    if changed and not allow_sensitive:
        raise RuntimeError(
            "Protected AltCards logic changed without explicit approval: "
            + ", ".join(changed)
            + ". Re-run only if the task intentionally changes this subsystem and allow_sensitive_changes=true."
        )


def enforce_file_allowlist(paths):
    unexpected = set(paths) - ALLOWED_FILES
    if unexpected:
        raise RuntimeError(f"Files outside allowlist rejected: {sorted(unexpected)}")
