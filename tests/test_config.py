"""
Tests for src.popularpages.config.AppConfig / user_agent.
"""

import src.popularpages.config as cfg


def _config_with_env() -> cfg.AppConfig:
    """Rebuild the AppConfig so credentials reflect the (monkeypatched) env."""
    return cfg.AppConfig(
        paths=cfg.config.paths,
        credentials=cfg.load_credentials(),
        pageviews=cfg.config.pageviews,
        wiki=cfg.config.wiki,
        project=cfg.config.project,
    )


def test_user_agent_identifies_project_and_url():
    ua = cfg.config.user_agent
    assert cfg.config.project.name in ua
    assert cfg.config.project.url in ua


def test_user_agent_includes_contact(monkeypatch):
    # When bot creds are present, the contact should be the bot user.
    monkeypatch.setenv("WIKIPEDIA_BOT_USERNAME", "ExampleBot@task")
    ua = _config_with_env().user_agent
    assert "contact: ExampleBot@task" in ua


def test_user_agent_falls_back_without_creds(monkeypatch):
    monkeypatch.delenv("WIKIPEDIA_BOT_USERNAME", raising=False)
    ua = _config_with_env().user_agent
    assert "contact: tool" in ua
