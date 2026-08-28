from __future__ import annotations

import logging

from ..config import config

from .maps import WikiReplicaMaps
from .replica_db import WikiReplicaBaseDB

logger = logging.getLogger(__name__)


class WikiReplicaDB(WikiReplicaBaseDB):
    """
    Toolforge-specific subclass that handles wiki shard resolution and credential loading.
    """

    def __init__(self, wiki_identifier: str) -> None:
        self.maps = WikiReplicaMaps.get_instance()

        # wiki_identifier can be "arwiki", "enwiki", "ar", etc.
        info = self.maps.resolve_wiki(wiki_identifier)
        if not info:
            logger.error("Unknown wiki: %s", wiki_identifier)
            raise ValueError(f"Unknown wiki: {wiki_identifier}")

        dbname = info["dbname"]
        slice_name = info.get("slice", "s1")  # Default to s1 if not found, but it should be there
        host = f"{slice_name}.analytics.db.svc.wikimedia.cloud"

        logger.info("Resolved wiki '%s' -> dbname='%s', host='%s'", wiki_identifier, dbname, host)

        super().__init__(
            dbname=f"{dbname}_p",
            host=host,
            user=config.db.user,
            password=config.db.password,
        )


__all__ = [
    "WikiReplicaDB",
]
