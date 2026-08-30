from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import app_config
from .replica_db import WikiReplicaBaseDB

logger = logging.getLogger(__name__)


@dataclass
class MapsData:
    last_cache_update: float
    data: dict[str, dict[str, str]]


class WikiReplicaMaps:
    """ """

    _instance = None

    @classmethod
    def get_instance(cls) -> WikiReplicaMaps:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(
        self,
        file_name: str = "wikimap.json",
        cache_ttl: int | None = None,
        save_new_data: bool = True,
    ) -> None:
        self.save_new_data = save_new_data
        self.file_name = file_name

        if cache_ttl is None:
            cache_ttl = app_config.db.cache_ttl

        self.cache_ttl = cache_ttl
        self._wiki_map: dict[str, dict[str, str]] = {}
        self._load_wiki_map()

    def _load_wiki_map(self) -> None:
        maps = self.load_local_wikimap()

        now = time.time()
        if maps.data and self.is_cache_valid(maps.last_cache_update, now):
            logger.debug("Using cached wiki map (%d entries)", len(maps.data))
            self._wiki_map = maps.data
            return

        logger.info("Loading wiki map from meta_p...")

        self._wiki_map = self._load_new_maps()

        logger.info("Loaded %d wiki entries from meta_p", len(self._wiki_map))

        self.save_wikimap(now)

    def is_cache_valid(self, last_cache_update: float, now: float) -> bool:
        # 0 means never expire
        if self.cache_ttl == 0:
            return True

        return now - last_cache_update < self.cache_ttl

    def _load_new_maps(self) -> dict[str, dict[str, str]]:
        # Note: meta_p table contains info about all wikis
        query = """
            SELECT lang, dbname, url, slice
            FROM wiki
            WHERE is_closed = 0
            AND url like "%.wikipedia.org"
        """
        new_map = {}

        if not app_config.db.has_db_data():
            logger.warning("No credentials for DB, skipping wiki map load")
            return new_map

        try:
            with WikiReplicaBaseDB(
                dbname="meta_p",
                host="s7.analytics.db.svc.wikimedia.cloud",
                user=app_config.db.user,
                password=app_config.db.password,
            ) as meta_db:

                results = meta_db.select(query)
                for row in results:
                    # We can index by dbname and lang (if family is wikipedia)
                    dbname = row["dbname"]
                    if not dbname:
                        continue

                    row["slice"] = str(row["slice"]).removesuffix(".labsdb")

                    # Don't use lang key, because (test.wikipedia.org, en.wikipedia.org) has same lang key
                    new_map[dbname] = row

        except Exception as e:
            logger.error(f"Failed to load wiki map: {e}")
            if not new_map:
                raise
        logger.debug("Resolved %d wiki entries from meta_p query", len(new_map))
        return new_map

    def resolve_wiki(self, identifier: str) -> dict[str, str] | None:
        # Try direct lookup
        if identifier in self._wiki_map:
            logger.debug("resolve_wiki: direct hit for '%s'", identifier)
            return self._wiki_map[identifier]

        # Try appending 'wiki' if it's just 'ar'
        if f"{identifier}wiki" in self._wiki_map:
            logger.debug("resolve_wiki: matched '%swiki' for '%s'", identifier, identifier)
            return self._wiki_map[f"{identifier}wiki"]

        logger.debug("resolve_wiki: no match for '%s'", identifier)
        return None

    def load_local_wikimap(self) -> MapsData:
        try:
            with open(self.file_name, encoding="utf-8") as f:
                data = json.load(f)
            return MapsData(
                last_cache_update=data["last_cache_update"],
                data=data["data"],
            )
        except FileNotFoundError:
            logger.info(f"Local wiki map file {self.file_name} not found. A new one will be created.")
        except Exception as e:
            logger.error(f"Failed to load {self.file_name}: {e}")
        return MapsData(
            last_cache_update=0,
            data={},
        )

    def save_wikimap(self, now) -> None:
        if not self.save_new_data:
            return
        data = {
            "last_cache_update": now,
            "data": self._wiki_map,
        }
        try:
            # Create the directory
            Path(self.file_name).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory for {self.file_name}: {e}")

        try:
            with open(self.file_name, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info("Saved wiki map to %s (%d entries)", self.file_name, len(self._wiki_map))
        except Exception as e:
            logger.error(f"Failed to save {self.file_name}: {e}")


__all__ = [
    "WikiReplicaMaps",
]
