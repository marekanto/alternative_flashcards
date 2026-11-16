import os

API_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
THINKING = os.getenv("DEEPSEEK_THINKING", "enabled")
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "3500"))

# HARDCODED — nie czytamy env, żeby żadna zmienna w workflow / GitHub
# Variables nie mogła tego nadpisać na 16000 (był taki bug).
MAX_CONTEXT_CHARS = 24000

MAX_SNIPPETS = int(os.getenv("MAX_SNIPPETS", "24"))

TARGET_FILE = "index.html"
ALLOWED_FILES = {TARGET_FILE}

PROTECTED_SYMBOLS = {
    "start", "grade", "ans", "undoAnswer", "save", "saveSettings",
    "Sync", "defaults", "state_init",
}
