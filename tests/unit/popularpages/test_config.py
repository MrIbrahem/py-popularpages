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
        """
        data example: {
            "en.wikipedia": {
                "database": "enwiki",
                "index": "User:Community Tech bot/Popular pages",
                "config": "Wikipedia:WikiProject/Popular pages config.json",
                "category": "Category:Lists of popular pages by WikiProject"
            },
            "ar.wikipedia": {
                "database": "arwiki",
                "index": "ويكيبيديا:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي",
                "config": "ويكيبيديا:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي/الإعدادات.json",
                "category": "تصنيف:قائمة الصفحات الأكثر مشاهدة حسب مشروع الويكي"
            }
        }
        """
        data = cfg.app_config.paths.load_wikis_config()
        assert isinstance(data, dict)
        assert len(data) > 0
        assert "en.wikipedia" in data
        assert set(data["en.wikipedia"]) == {"database", "index", "config", "category"}
