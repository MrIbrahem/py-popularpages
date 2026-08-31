from datetime import date, datetime

from src.py_port.popularpages.mapping import MonthDate, WikiProjectConfig


class TestPreviousMonthRange:
    """Tests for computing the previous calendar month's date range."""

    def test_previous_month_range_mid_year(self):

        obj = MonthDate.from_today(date(2024, 6, 15))
        assert obj.start == date(2024, 5, 1)
        assert obj.end == date(2024, 5, 31)

    def test_previous_month_range_year_boundary(self):

        obj = MonthDate.from_today(date(2024, 1, 10))
        assert obj.start == date(2023, 12, 1)
        assert obj.end == date(2023, 12, 31)

    # ---------------------------------------------------
    # Pure unit tests (no network/credentials required)
    # ---------------------------------------------------
    def test_previous_month_range_midyear(self):
        today = datetime(2023, 6, 15, 10, 30, 0)
        obj = MonthDate.from_today(today)
        assert (obj.start.year, obj.start.month, obj.start.day) == (2023, 5, 1)
        assert (obj.end.year, obj.end.month, obj.end.day) == (2023, 5, 31)

    def test_previous_month_range_year_boundary2(self):
        today = datetime(2023, 1, 10, 0, 0, 0)
        obj = MonthDate.from_today(today)
        assert (obj.start.year, obj.start.month, obj.start.day) == (2022, 12, 1)
        assert (obj.end.year, obj.end.month, obj.end.day) == (2022, 12, 31)

    def test_previous_month_range_days_in_month(self):
        # February in a non-leap year.
        today = datetime(2023, 3, 5)
        obj = MonthDate.from_today(today)
        days_in_month = (obj.end - obj.start).days + 1
        assert days_in_month == 28


class TestProjectReportTitles:
    """
    Tests for the staticmethod that maps project report titles.
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
    """
    Tests for parsing WikiProjectConfig from JSON and related helpers.
    """

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
        assert WikiProjectConfig.trim_report_prefix("Popular pages/Popular pages") == "Popular_pages/Popular_pages"

    def test_is_incomplete_true_when_name_missing(self):
        cfg = WikiProjectConfig(
            project_main_page="Foo",
            Report="Foo/Popular pages",
            report_without_ns="Foo/Popular_pages",
            Limit="10",  # pyright: ignore[reportArgumentType]
            Name="",
        )
        assert cfg.is_incomplete() is True

    def test_is_incomplete_false_when_complete(self):
        cfg = WikiProjectConfig.from_json("Foo", data=self.DATA)
        assert cfg.is_incomplete() is False
