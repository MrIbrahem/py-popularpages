"""
Tests for src.popularpages.config.user_agent.
"""

from src.popularpages.config import PROJECT_NAME, PROJECT_URL, user_agent


def test_user_agent_identifies_project_and_url():
    ua = user_agent()
    assert PROJECT_NAME in ua
    assert PROJECT_URL in ua


def test_user_agent_includes_contact(monkeypatch):
    # When bot creds are present, the contact should be the bot user.
    monkeypatch.setenv("WIKIPEDIA_BOT_USERNAME", "ExampleBot@task")
    ua = user_agent()
    assert "contact: ExampleBot@task" in ua


def test_user_agent_falls_back_without_creds(monkeypatch):
    monkeypatch.delenv("WIKIPEDIA_BOT_USERNAME", raising=False)
    ua = user_agent()
    assert "contact: tool" in ua
