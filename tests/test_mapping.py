""" """

from src.popularpages.mapping import WikiProjectConfig
from src.popularpages.wiki_repository import WikiRepository


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
        result2 = WikiRepository._project_report_titles(config_obj)

        obj_1 = config_obj[0]

        assert obj_1.project_main_page == result2[obj_1.report_without_ns.replace(" ", "_")]
