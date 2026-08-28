""" """

from src.popularpages.mapping import WikiProjectConfig


class TestProjectRreportTitles:
    """
    tests for staticmethods _project_report_titles
    """

    def test_project_report_titles(self):
        config_json_data = {
            "Wikipedia:WikiProject Slovakia": {
                "Report": "Wikipedia:WikiProject Slovakia/Popular pages",
                "Limit": "500",
                "Name": "Slovakia",
            },
            "Wikipedia:WikiProject Video games/Video game characters": {
                "Report": "Wikipedia:WikiProject Video games/Video game characters/Popular pages",
                "Limit": 500,
                "Name": "Video games/Characters task force",
            },
            "Wikipedia:WikiProject Magic: The Gathering": {
                "Report": "Wikipedia:WikiProject Magic: The Gathering/Popular pages",
                "Limit": "500",
                "Name": "Magic: The Gathering",
            },
            "Wikipedia:WikiProject Forestry": {
                "Report": "Wikipedia:WikiProject_Forestry/Popular pages",
                "Limit": "500",
                "Name": "Forestry",
            },
        }

        config_obj = WikiProjectConfig.from_json_list(config_json_data)
        result2 = {x.report_without_ns: x.project_main_page for x in config_obj}

        obj_1 = config_obj[0]

        assert obj_1.project_main_page == result2[obj_1.report_without_ns]


class TestWikiProjectConfigParsing:
    DATA = {"Report": "Foo/Popular pages", "Limit": "10", "Name": "Foo"}

    def test_from_json_returns_config(self):
        cfg = WikiProjectConfig.from_json("Foo", data=self.DATA)
        assert isinstance(cfg, WikiProjectConfig)
        assert cfg.Name == "Foo"
        assert cfg.Limit == 10

    def test_from_json_dict_with_dict(self):
        cfgs = WikiProjectConfig.from_json_dict({"Foo": self.DATA})
        assert cfgs["Foo"].project_main_page == "Foo"

    def test_from_json_list_multiple(self):
        cfgs = WikiProjectConfig.from_json_list({"Foo": self.DATA, "Bar": self.DATA})
        assert len(cfgs) == 2

    def test_trim_report_prefix_strips_namespace(self):
        assert (
            WikiProjectConfig.trim_report_prefix("Wikipedia:WikiProject X/Popular pages")
            == "WikiProject_X/Popular_pages"
        )

    def test_trim_report_prefix_no_colon(self):
        assert (
            WikiProjectConfig.trim_report_prefix("Popular pages/Popular pages")
            == "Popular_pages/Popular_pages"
        )

    def test_is_incomplete_true_when_name_missing(self):
        cfg = WikiProjectConfig(
            project_main_page="Foo",
            Report="Foo/Popular pages",
            report_without_ns="Foo/Popular_pages",
            Limit="10",
            Name="",
        )
        assert cfg.is_incomplete() is True

    def test_is_incomplete_false_when_complete(self):
        cfg = WikiProjectConfig.from_json("Foo", data=self.DATA)
        assert cfg.is_incomplete() is False
