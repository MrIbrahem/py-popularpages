"""Minimal i18n replacement for krinkle/intuition (PHP).

Loads messages from messages/{lang}.json (the exact same files used by the
PHP version -- they are NOT modified as part of this migration) and performs
positional-variable substitution using the Wikimedia convention of $1, $2,
... placeholders, matching Intuition::msg()'s behavior.

Falls back to English for any key missing in the requested language.
"""

from __future__ import annotations

import json
from pathlib import Path

MESSAGES_DIR = Path(__file__).resolve().parent.parent.parent / "messages"


class I18n:
    """Minimal replacement for Krinkle's Intuition translation service.

    Loads ``messages/{lang}.json`` files and substitutes ``$1``, ``$2``, ...
    positional placeholders. Falls back to English for missing keys.
    """

    def __init__(self, lang: str = "en"):
        self.lang = lang
        self._cache: dict[str, dict] = {}

    def _load(self, lang: str) -> dict:
        if lang not in self._cache:
            path = MESSAGES_DIR / f"{lang}.json"
            with path.open(encoding="utf-8") as f:
                self._cache[lang] = json.load(f)
        return self._cache[lang]

    def msg(self, key: str, variables: list[str] | None = None) -> str:
        variables = variables or []
        messages = self._load(self.lang)
        text = messages.get(key)
        if text is None:
            text = self._load("en").get(key, key)
        for i, value in enumerate(variables, start=1):
            text = text.replace(f"${i}", str(value))
        return text
