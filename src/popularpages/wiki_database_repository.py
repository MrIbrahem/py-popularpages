"""
Replica database access, extracted from WikiRepository.

Encapsulates all direct MySQL/PyMySQL interaction against the Wikimedia
replica database (connection handling + raw SQL queries). Kept separate
from WikiRepository, which deals with the MediaWiki Action API.
"""

from __future__ import annotations

import pymysql
import pymysql.cursors

from .logger import log_to_file
from .utils import first_of_this_month_timestamp, mediawiki_timestamp_to_epoch


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
        return bytes(value).decode("utf-8")
    return value


class WikiDatabaseRepository:
    """
    Handles all replica-database access (connection + raw SQL queries)
    for a given wiki.
    """

    def __init__(self, wiki: str, creds: dict[str, str], wiki_config: dict, username: str):
        """
        :param wiki: Wiki in the form lang.project, e.g. 'en.wikipedia'.
        :param creds: Credentials dict (dbhost, dbuser, dbpass, dbport, ...).
        :param wiki_config: This wiki's config (index/config/category/database).
        :param username: Bot username (without the @clientname suffix), used
            to look up the bot's own edits.
        """
        self.wiki = wiki
        self.creds = creds
        self.wiki_config = wiki_config
        self.username = username

    # -- Connection -----------------------------------------------------

    # -- Database -----------------------------------------

    def _connect(self) -> pymysql.connections.Connection:
        # In production, the host is *.web.db.svc.wikimedia.cloud, where the
        # asterisk is dynamically replaced with the database name.
        db_name = self.wiki_config["database"].removesuffix("_p")
        host = self.creds["dbhost"].replace("*", db_name)
        return pymysql.connect(
            host=host,
            user=self.creds["dbuser"],
            password=self.creds["dbpass"],
            database=f"{db_name}_p",
            port=int(self.creds["dbport"]),
            cursorclass=pymysql.cursors.DictCursor,
        )

    # -- Queries ----------------------------------------------------------

    def get_projects_with_last_bot_timestamp(self, projects: dict[str, str]) -> list[dict]:
        """
        Get timestamps of the bot's last edits for the given WikiProjects.

        :param projects: Mapping of db-key page title -> WikiProject name.
        :return: List of dicts with 'page_title', 'rev_timestamp', and 'name'.
        """
        log_to_file("Fetching timestamps of the bot's last edits", self.wiki)

        titles = list(projects.keys())
        if not titles:
            return []

        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                placeholders = ", ".join(["%s"] * len(titles))
                cursor.execute(
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
                rows = cursor.fetchall()
        finally:
            conn.close()

        # PyMySQL returns the BINARY(14) rev_timestamp and VARBINARY page_title
        # as bytes; decode to str before using page_title as a config-key lookup
        # and before the timestamps are parsed by strptime elsewhere.

        for row in rows:
            row["page_title"] = _to_str(row["page_title"])
            row["rev_timestamp"] = _to_str(row["rev_timestamp"])
            row["name"] = projects[row["page_title"]]

        return rows

    def get_stale_project_names(self, config: dict, projects: dict[str, str]) -> set[str]:
        """
        Determine which WikiProject names (from `config`) have already been
        updated this month, based on the bot's last-edit timestamps.

        :param config: Full JSON config (project_name -> info).
        :param projects: Mapping of db-key page title -> WikiProject name,
            matching `config`'s keys.
        :return: Set of project names that were already updated this cycle
            (i.e. NOT stale) -- callers typically pop these out of `config`.
        """
        bot_timestamps = self.get_projects_with_last_bot_timestamp(projects)
        first_of_this_month = first_of_this_month_timestamp()

        updated_names: set[str] = set()
        for row in bot_timestamps:
            rev_timestamp = mediawiki_timestamp_to_epoch(row["rev_timestamp"])
            if rev_timestamp >= first_of_this_month:
                updated_names.add(row["name"])

        return updated_names

    def get_project_pages(self, project: str) -> list[dict]:
        """
        Get titles & assessments for all pages in a WikiProject.

        :param project: Name of the project, e.g. 'Medicine'.
        :return: List of rows with page_title, pa_class, pa_importance, redir_title.
        """
        log_to_file(f"Fetching pages and assessments for project {project}", self.wiki)

        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
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
                rows = cursor.fetchall()
        finally:
            conn.close()

        # MediaWiki returns page_title/redir_title as VARBINARY and
        # pa_class/pa_importance as VARBINARY/strings; PyMySQL yields bytes for
        # the binary ones. Decode so downstream URL/strptime/template code sees str.
        for row in rows:
            row["page_title"] = _to_str(row["page_title"])
            row["redir_title"] = _to_str(row["redir_title"])
            row["pa_class"] = _to_str(row["pa_class"])
            row["pa_importance"] = _to_str(row["pa_importance"])
        return rows
