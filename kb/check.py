"""Read-only configuration preflight. Does not call Telegram or AI."""
import json
import os

import db
from kb.config import ROOT


def main():
    for key in ("DATABASE_URL", "OPENROUTER_API_KEY", "MODEL", "TELEGRAM_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "WEB_PASSWORD", "EMBEDDING_MODEL"):
        print(f"{key}: {'set' if os.getenv(key, '').strip() else 'MISSING'}")
    configs = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    print(f"Sources: {len(configs)}; EnjoySurvey PDF: {'present' if list(ROOT.glob('ES 4.1*.pdf')) else 'missing'}")
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        print("PostgreSQL: OK")
    except Exception as error:
        print("PostgreSQL: " + type(error).__name__)
