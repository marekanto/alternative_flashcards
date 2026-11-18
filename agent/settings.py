import os

API_BASE = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Domyślnie WYŁĄCZONE myślenie (oszczędza tokeny w code_agent i qa_agent).
# task_refiner jawnie włącza thinking="enabled" — tam rozumowanie pomaga.
THINKING = os.getenv("DEEPSEEK_THINKING", "disabled")

# 3500 było za mało — model ucinał duże patche w połowie JSON-a.
# 8000 to bezpieczny zapas; DeepSeek obsługuje do 8192 tokenów outputu.
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8000"))

# HARDCODED — nie czytamy env, żeby żadna zmienna w workflow / GitHub
# Variables nie mogła tego nadpisać na 16000 (był taki bug).
MAX_CONTEXT_CHARS = 32000
MAX_SNIPPETS = int(os.getenv("MAX_SNIPPETS", "24"))

TARGET_FILE = "index.html"
ALLOWED_FILES = {TARGET_FILE}

PROTECTED_SYMBOLS = {
    "start", "grade", "ans", "undoAnswer", "save", "saveSettings",
    "Sync", "defaults", "state_init",
}
