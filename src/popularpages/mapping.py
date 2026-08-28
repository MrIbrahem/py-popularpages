""" """

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class WikiProjectConfig:
    """
    data example:
    {
        "Wikipedia:WikiProject Dinosaurs": {
            "Report": "Wikipedia:WikiProject Dinosaurs/Popular pages",
            "Limit": "500",
            "Name": "Dinosaurs"
        }
    }
    """

    project_main_page: str
    Report: str
    report_without_ns: str
    Limit: int
    Name: str
    Updated: str | None = None

    @classmethod
    def from_json(cls, project_main_page: str, data: dict[str, Any]) -> WikiProjectConfig:
        return cls(
            project_main_page=project_main_page,
            Report=data["Report"],
            report_without_ns=cls.trim_report_prefix(data["Report"]),
            Limit=int(data["Limit"]),
            Name=data["Name"],
            Updated=data.get("Updated"),
        )

    @classmethod
    def trim_report_prefix(cls, report: str) -> str:
        # FIXME: assumes reports are in the Project namespace (matches PHP FIXME).
        # db_key = report.split(":", 1)[-1]
        db_key = re.sub(r"^.*?:", "", report)
        return db_key

    @classmethod
    def from_json_list(cls, data: dict[str, dict[str, Any]]) -> list[WikiProjectConfig]:
        return [cls.from_json(project_main_page, data) for project_main_page, data in data.items()]


__all__ = [
    "WikiProjectConfig",
]
