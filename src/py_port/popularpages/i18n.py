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

from .config import app_config

logger = logging.getLogger(__name__)


class I18n:
    """
    Minimal replacement for Krinkle's Intuition translation service.

    Loads ``messages/{lang}.json`` files and substitutes ``$1``, ``$2``, ...
    positional placeholders. Falls back to English for missing keys.
    """

    def __init__(self, lang: str = app_config.wiki.fallback_lang):
        self.lang = lang
        self._cache: dict[str, dict] = {}

    def _load(self, lang: str) -> dict:
        if lang not in self._cache:
            path = app_config.paths.messages_dir / f"{lang}.json"
            if not path.exists():
                logger.info("No messages file for lang '%s'; falling back", lang)
                self._cache[lang] = {}
            else:
                logger.debug("Loading messages for lang '%s' from %s", lang, path)
                with path.open(encoding="utf-8") as f:
                    self._cache[lang] = json.load(f)
        return self._cache[lang]

    def msg(self, key: str, variables: list[str] | None = None) -> str:
        """
        Return the translated, variable-substituted message for `key`.

        Loads the message map for the current language (falling back to English
        and then to the raw key if missing), then substitutes each ``$1``,
        ``$2``, ... placeholder with the corresponding value from ``variables``.

        Args:
            key (str): Message key, as defined in messages/{lang}.json.
            variables (list[str] | None): Positional values to substitute for $1, $2, ...

        Returns:
            str: The rendered message. Falls back to English, then to the
                raw key itself, if no translation is found.
        """
        variables = variables or []
        messages = self._load(self.lang)
        text = messages.get(key)

        if text is None and self.lang != app_config.wiki.fallback_lang:
            logger.debug("Message key '%s' missing in '%s'; trying fallback", key, self.lang)
            text = self._load(app_config.wiki.fallback_lang).get(key)

        if text is None:
            logger.debug("Message key '%s' not found in any lang; using raw key", key)
            text = key

        for index, value in enumerate(variables, start=1):
            text = text.replace(f"${index}", str(value))

        logger.debug("Resolved message '%s' -> '%s'", key, text)
        return text


__all__ = [
    "I18n",
]
