import os

API_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")   # było "disabled"
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "3500"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "24000"))
MAX_SNIPPETS = int(os.getenv("MAX_SNIPPETS", "24"))
TARGET_FILE = "index.html"
ALLOWED_FILES = {TARGET_FILE}
PROTECTED_SYMBOLS = {
    "start", "grade", "ans", "undoAnswer", "save", "saveSettings",
    "Sync", "defaults", "state_init"
}
