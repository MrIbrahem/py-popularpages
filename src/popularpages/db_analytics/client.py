from __future__ import annotations

import logging

from .maps import WikiReplicaMaps
from .replica_db import WikiReplicaBaseDB

logger = logging.getLogger(__name__)


class WikiReplicaDB(WikiReplicaBaseDB):
    """
    Toolforge-specific subclass that handles wiki shard resolution and credential loading.
    """

    def __init__(self, wiki_identifier: str) -> None:
        self.maps = WikiReplicaMaps.get_instance()
        # self.maps = WikiReplicaMaps(
        #     WikiReplicaBaseDB(
        #         dbname="meta_p",
        #         host="s7.analytics.db.svc.wikimedia.cloud",
        #     )
        # )

        # wiki_identifier can be "arwiki", "enwiki", "ar", etc.
        info = self.maps.resolve_wiki(wiki_identifier)
        if not info:
            raise ValueError(f"Unknown wiki: {wiki_identifier}")

        dbname = info["dbname"]
        slice_name = info.get("slice", "s1")  # Default to s1 if not found, but it should be there
        host = f"{slice_name}.analytics.db.svc.wikimedia.cloud"

        super().__init__(dbname=f"{dbname}_p", host=host)


__all__ = [
    "WikiReplicaDB",
]
