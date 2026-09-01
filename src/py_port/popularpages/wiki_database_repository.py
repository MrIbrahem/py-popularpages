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

logger = logging.getLogger(__name__)


class WikiDatabaseRepository:
    """
    Handles all replica-database access (connection + raw SQL queries)
    for a given wiki.
    """

    def __init__(self, wiki: str, wiki_config: dict, username: str):
        """
        Initialize replica-database access for a single wiki.

        Stores the wiki identity, its config, and the bot username, then opens a
        connection to the wiki's replica database (the ``_p`` suffix is stripped
        from the configured database name). All subsequent queries run through
        this connection.

        Args:
            wiki (str): Wiki in the form lang.project, e.g. 'en.wikipedia'.
            wiki_config (dict): This wiki's config (index/config/category/database).
            username (str): Bot username (without the @clientname suffix), used
                to look up the bot's own edits.
        """
        self.wiki = wiki
        self.wiki_config = wiki_config
        self.username = username

        db_name = self.wiki_config["database"].removesuffix("_p")
        logger.debug("WikiDatabaseRepository for wiki '%s' using db '%s'", wiki, db_name)
        self.db = WikiReplicaDB(db_name)

    # -- Database ---------------------------------------------------

    def _get_projects_timestamps(self, titles: list[str]) -> list[dict[str, Any]]:
        """
        Get timestamps of the bot's last edits for the given WikiProjects.

        Runs a SQL query against the replica database that finds, for each
        requested page title, the most recent revision authored by the bot in
        the Project namespace (namespace 4) and returns its timestamp.

        Args:
            titles (list[str]): Mapping of db-key page title -> WikiProject name.

        Returns:
            list[dict[str, Any]]: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """

        placeholders = ", ".join(["%s"] * len(titles))
        logger.debug("Fetching timestamps for %d project(s)", len(titles))

        # The replica DB stores page_title with underscores, but callers pass
        # display-form titles (spaces). Convert back to DB form for the lookup,
        # and normalise the returned page_title to spaces at the DB boundary so
        # no downstream consumer has to.
        db_titles = [t.replace(" ", "_") for t in titles]

        rows = self.db.select(
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
            (self.username, *db_titles),
        )

        for row in rows:
            # Normalise db-key underscores to display spaces.
            row["page_title"] = (row["page_title"] or "").replace("_", " ")

        return rows  # pyright: ignore[reportReturnType]

    def _get_project_pages(self, project: str) -> list[dict[str, Any]]:
        """
        Get titles & assessments for all pages in a WikiProject.

        Queries the replica database for every page in the given project's
        namespace, returning its page title, assessment class/importance, and
        the title of its redirect (if any).

        Args:
            project (str): Name of the project, e.g. 'Medicine'.

        Returns:
            list[dict[str, Any]]: List of rows with page_title, pa_class, pa_importance, redir_title.
        """

        logger.debug("Fetching pages and assessments for project '%s'", project)
        query = """
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
        """
        rows = self.db.select_safe(query, (project,))

        # MediaWiki stores titles with underscores; normalise to display spaces
        # at the DB boundary so every consumer receives titles without '_'.
        for row in rows:
            row["page_title"] = (row["page_title"] or "").replace("_", " ")
            row["redir_title"] = (row["redir_title"] or "").replace("_", " ")

        return rows

    # -- Queries ---------------------------------------------------

    def get_projects_timestamps(self, titles: list[str]) -> list[dict[str, Any]]:
        """
        Get timestamps of the bot's last edits for the given WikiProjects.

        Public wrapper around :meth:`_get_projects_timestamps` that runs the
        query and returns the raw rows (binary columns already resolved by
        ``db.select``).

        Args:
            titles (list[str]): Mapping of db-key page title -> WikiProject name.

        Returns:
            list[dict[str, Any]]: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """
        logger.info("[%s] Fetching timestamps of the bot's last edits", self.wiki)

        rows = self._get_projects_timestamps(titles)
        logger.debug("Retrieved timestamps for %d project(s)", len(rows))

        # PyMySQL returns BINARY/VARBINARY columns (page_title, rev_timestamp) but db.select already resolve_bytes
        return rows

    def get_project_pages(self, project: str) -> list[dict[str, Any]]:
        """
        Get titles & assessments for all pages in a WikiProject.

        Public wrapper around :meth:`_get_project_pages` that runs the query and
        returns the raw rows for the given project.

        Args:
            project (str): Name of the project, e.g. 'Medicine'.

        Returns:
            list[dict[str, Any]]: List of rows with page_title, pa_class, pa_importance, redir_title.
        """
        logger.debug("Fetching pages for project '%s'", project)
        logger.info("[%s] Fetching pages and assessments for project %s", self.wiki, project)

        rows = self._get_project_pages(project)
        logger.debug("Retrieved %d page(s) for project '%s'", len(rows), project)

        return rows


__all__ = [
    "WikiDatabaseRepository",
]
