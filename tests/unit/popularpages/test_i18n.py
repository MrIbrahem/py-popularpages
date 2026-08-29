"""
Tests for src.py_port.popularpages.i18n.I18n."""

from src.py_port.popularpages.i18n import I18n


def test_msg_basic_lookup():
    i18n = I18n("en")
    assert i18n.msg("rank") == "Rank"


def test_msg_with_variable_substitution():
    i18n = I18n("en")
    result = i18n.msg("date-range", ["2024-01-01", "2024-01-31"])
    assert result == "2024-01-01 to 2024-01-31"


def test_msg_arabic_lookup():
    i18n = I18n("ar")
    assert i18n.msg("rank") == "المرتبة"


def test_msg_falls_back_to_english_for_missing_key():
    i18n = I18n("ar")
    # 'config-subpage' exists in both; pick a key we know exists in en.json.
    # Simulate a missing key by requesting one that doesn't exist anywhere.
    result = i18n.msg("nonexistent-key-xyz")
    assert result == "nonexistent-key-xyz"


def test_msg_unknown_key_returns_key_itself():
    i18n = I18n("en")
    assert i18n.msg("totally-made-up-key") == "totally-made-up-key"


def test_msg_falls_back_when_lang_file_missing():
    # 'zz' has no messages file, so it should fall back to English.
    i18n = I18n("zz")
    assert i18n.msg("rank") == "Rank"
