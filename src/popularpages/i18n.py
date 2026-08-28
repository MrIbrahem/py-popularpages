"""
Minimal i18n replacement for krinkle/intuition (PHP).

Loads messages from messages/{lang}.json (the exact same files used by the
PHP version -- they are NOT modified as part of this migration) and performs
positional-variable substitution using the Wikimedia convention of $1, $2,
... placeholders, matching Intuition::msg()'s behavior.

Falls back to English for any key missing in the requested language.
"""

from __future__ import annotations

import json
import logging

from .config import FALLBACK_LANG, MESSAGES_DIR

logger = logging.getLogger(__name__)


class I18n:
    """
    Minimal replacement for Krinkle's Intuition translation service.

    Loads ``messages/{lang}.json`` files and substitutes ``$1``, ``$2``, ...
    positional placeholders. Falls back to English for missing keys.
    """

    def __init__(self, lang: str = FALLBACK_LANG):
        self.lang = lang
        self._cache: dict[str, dict] = {}

    def _load(self, lang: str) -> dict:
        if lang not in self._cache:
            path = MESSAGES_DIR / f"{lang}.json"
            if not path.exists():
                self._cache[lang] = {}
            else:
                with path.open(encoding="utf-8") as f:
                    self._cache[lang] = json.load(f)
        return self._cache[lang]

    def msg(self, key: str, variables: list[str] | None = None) -> str:
        """
        Return the translated, variable-substituted message for `key`.

        :param key: Message key, as defined in messages/{lang}.json.
        :param variables: Positional values to substitute for $1, $2, ...
        :return: The rendered message. Falls back to English, then to the
            raw key itself, if no translation is found.
        """
        variables = variables or []
        messages = self._load(self.lang)
        text = messages.get(key)

        if text is None and self.lang != FALLBACK_LANG:
            text = self._load(FALLBACK_LANG).get(key)

        if text is None:
            text = key

        for index, value in enumerate(variables, start=1):
            text = text.replace(f"${index}", str(value))

        return text
