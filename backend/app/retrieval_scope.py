from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizedRetrievalScope:
    user_id: str
    topic_id: str
    active_run_ids: frozenset[str]

    def allows(self, *, user_id: str, ingestion_run_id: str) -> bool:
        return user_id == self.user_id and ingestion_run_id in self.active_run_ids