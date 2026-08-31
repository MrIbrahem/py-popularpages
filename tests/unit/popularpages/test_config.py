"""
Tests for src.py_port.popularpages.config.AppConfig / user_agent.
"""

import src.py_port.popularpages.config as cfg

# ---------------------------------------------------
# 1. Tests for User-Agent
# ---------------------------------------------------


class TestUserAgent:
    """Tests for the configured User-Agent string."""


# ---------------------------------------------------
# 2. Tests for loading wiki config and credentials
# ---------------------------------------------------


class TestConfigLoading:
    """Tests for loading wiki config and credentials."""

    def test_load_wikis_config_reads_yaml(self):
        data = cfg.app_config.paths.load_wikis_config()
        assert isinstance(data, dict)
        assert len(data) > 0
