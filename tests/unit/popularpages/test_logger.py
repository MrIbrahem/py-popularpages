"""
Tests for src.popularpages.logger."""

import dataclasses

import src.popularpages.config as cfg
import src.popularpages.logger as logger_module
from src.popularpages.logger import log_to_file


def _with_log_dir(tmp_path):
    new_cfg = dataclasses.replace(
        cfg.config,
        paths=dataclasses.replace(cfg.config.paths, log_dir=tmp_path),
    )
    return new_cfg


def test_log_to_file_writes_expected_line(tmp_path, monkeypatch):
    monkeypatch.setattr(logger_module, "config", _with_log_dir(tmp_path))

    log_to_file("Test message", "en.wikipedia")

    log_file = tmp_path / "log-en.wikipedia.txt"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert content.endswith("Test message\n")
    # Line should start with a timestamp like '2024-01-01 12:00:00'.
    assert content[:4].isdigit()


def test_log_to_file_appends_multiple_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(logger_module, "config", _with_log_dir(tmp_path))

    log_to_file("First", "ar.wikipedia")
    log_to_file("Second", "ar.wikipedia")

    log_file = tmp_path / "log-ar.wikipedia.txt"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("First")
    assert lines[1].endswith("Second")
