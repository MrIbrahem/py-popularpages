"""
Replica database access, extracted from WikiRepository.

Encapsulates all direct MySQL/PyMySQL interaction against the Wikimedia
replica database (connection handling + raw SQL queries). Kept separate
from WikiRepository, which deals with the MediaWiki Action API.
"""

from __future__ import annotations

import logging
from typing import Any

from .db_analytics import WikiReplicaDB
from .logger import log_to_file

logger = logging.getLogger(__name__)


def _to_str(value: object) -> object:
    """
    Normalize a value that may come back from PyMySQL as ``bytes``.

    MediaWiki stores ``page_title`` as ``VARBINARY`` and ``rev_timestamp`` as
    ``BINARY``. PHP's ``mysqli`` returns these as strings, but PyMySQL returns
    ``bytes`` for binary columns by default. Decoding at the cursor boundary
    keeps the rest of the pipeline (URL building, strptime, template rendering)
    string-based and consistent with the PHP behavior. Non-bytes values are
    returned unchanged.
    """
    if isinstance(value, bytes | bytearray):
        decoded = bytes(value).decode("utf-8")
        logger.debug("Decoded %d bytes to str", len(value))
        return decoded
    return value


class WikiDatabaseRepository:
    """
    Handles all replica-database access (connection + raw SQL queries)
    for a given wiki.
    """

    def __init__(self, wiki: str, wiki_config: dict, username: str):
        """
        :param wiki: Wiki in the form lang.project, e.g. 'en.wikipedia'.
        :param wiki_config: This wiki's config (index/config/category/database).
        :param username: Bot username (without the @clientname suffix), used
            to look up the bot's own edits.
        """
        self.wiki = wiki
        self.wiki_config = wiki_config
        self.username = username

        db_name = self.wiki_config["database"].removesuffix("_p")
        logger.debug("WikiDatabaseRepository for wiki '%s' using db '%s'", wiki, db_name)
        self.db = WikiReplicaDB(db_name)

    # -- Database -----------------------------------------

    def _get_projects_timestamps(self, titles: list[str]) -> list[dict[str, Any]]:
        """
        Get timestamps of the bot's last edits for the given WikiProjects.

        :param projects: Mapping of db-key page title -> WikiProject name.
        :return: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """

        placeholders = ", ".join(["%s"] * len(titles))
        logger.debug("Fetching timestamps for %d project(s)", len(titles))

        rows = self.db.select_safe(
            f"""
            SELECT page_title, MAX(rev_timestamp) AS rev_timestamp
            FROM revision_userindex
            JOIN page ON rev_page = page_id
            WHERE rev_actor = (
                SELECT actor_id
                FROM actor
                WHERE actor_name = %s
            )
            AND page_title IN ({placeholders})
            AND page_namespace = 4 -- FIXME: assumes reports are in the Project namespace
            GROUP BY page_title
            """,
            (self.username, *titles),
        )

        return rows  # pyright: ignore[reportReturnType]

    def _get_project_pages(self, project: str) -> list[dict]:
        """
        Get titles & assessments for all pages in a WikiProject.

        :param project: Name of the project, e.g. 'Medicine'.
        :return: List of rows with page_title, pa_class, pa_importance, redir_title.
        """

        logger.debug("Fetching pages and assessments for project '%s'", project)
        rows = self.db.select_safe(
            """
            SELECT page_title, pa_class, pa_importance, (
                SELECT rp.page_title
                FROM page rp
                WHERE rd_from = page_id
                AND rp.page_namespace = 0
            ) AS redir_title
            FROM page
            JOIN page_assessments ON page_id = pa_page_id
            LEFT OUTER JOIN redirect ON rd_title = page_title AND rd_namespace = 0
            WHERE pa_project_id = (
                SELECT pap_project_id
                FROM page_assessments_projects
                WHERE pap_project_title = %s
            )
            AND page_namespace = 0
            """,
            (project,),
        )

        return rows  # pyright: ignore[reportReturnType]

    # -- Queries ----------------------------------------------------------

    def get_projects_timestamps(self, titles: list[str]) -> list[dict]:
        """
        Get timestamps of the bot's last edits for the given WikiProjects.

        :param titles: Mapping of db-key page title -> WikiProject name.
        :return: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """
        log_to_file("Fetching timestamps of the bot's last edits", self.wiki)

        rows = self._get_projects_timestamps(titles)
        logger.debug("Retrieved timestamps for %d project(s)", len(rows))

        # PyMySQL returns the BINARY(14) rev_timestamp and VARBINARY page_title
        # as bytes; decode to str before using page_title as a config-key lookup
        # and before the timestamps are parsed by strptime elsewhere.

        for row in rows:
            row["page_title"] = _to_str(row["page_title"])
            row["rev_timestamp"] = _to_str(row["rev_timestamp"])
            # row["name"] = projects[row["page_title"]]

        return rows

    def get_project_pages(self, project: str) -> list[dict]:
        """
        Get titles & assessments for all pages in a WikiProject.

        :param project: Name of the project, e.g. 'Medicine'.
        :return: List of rows with page_title, pa_class, pa_importance, redir_title.
        """
        log_to_file(f"Fetching pages and assessments for project {project}", self.wiki)

        rows = self._get_project_pages(project)
        logger.debug("Retrieved %d page(s) for project '%s'", len(rows), project)

        # MediaWiki returns page_title/redir_title as VARBINARY and
        # pa_class/pa_importance as VARBINARY/strings; PyMySQL yields bytes for
        # the binary ones. Decode so downstream URL/strptime/template code sees str.
        for row in rows:
            row["page_title"] = _to_str(row["page_title"])
            row["redir_title"] = _to_str(row["redir_title"])
            row["pa_class"] = _to_str(row["pa_class"])
            row["pa_importance"] = _to_str(row["pa_importance"])

        return rows
