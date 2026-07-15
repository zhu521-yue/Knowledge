"""deduplicate web sources by topic and normalized URL"""

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0011_source_url_identity"
down_revision: str | None = "0010_source_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def _candidate_id(topic_id: str, url: str) -> str:
    identity = f"knowledge:web-source:{topic_id}:{_normalized_url(url)}"
    return str(uuid5(NAMESPACE_URL, identity))


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column("duplicate_of_source_document_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_source_documents_duplicate_of",
        "source_documents",
        "source_documents",
        ["duplicate_of_source_document_id"],
        ["id"],
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT d.id, d.user_id, d.state, d.created_at, t.topic_id,
                   r.original_url, r.created_at AS revision_created_at
            FROM source_documents d
            JOIN topic_source_documents t ON t.source_document_id = d.id
            JOIN source_revisions r ON r.source_document_id = d.id
            WHERE d.input_type = 'web_url' AND r.original_url IS NOT NULL
            ORDER BY d.created_at, d.id, r.created_at, r.id, t.topic_id
            """
        )
    ).mappings()
    sources: dict[str, dict[str, object]] = {}
    topics_by_source: dict[str, set[str]] = {}
    for row in rows:
        source_id = str(row["id"])
        topics_by_source.setdefault(source_id, set()).add(str(row["topic_id"]))
        sources.setdefault(source_id, dict(row))

    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for source_id, source in sources.items():
        topic_ids = topics_by_source[source_id]
        if len(topic_ids) != 1:
            continue
        topic_id = next(iter(topic_ids))
        normalized_url = _normalized_url(str(source["original_url"]))
        key = (str(source["user_id"]), topic_id, normalized_url)
        groups.setdefault(key, []).append(source)

    for (_user_id, topic_id, normalized_url), candidates in groups.items():
        canonical = min(
            candidates,
            key=lambda item: (
                0 if item["state"] == "active" else 1,
                str(item["created_at"]),
                str(item["id"]),
            ),
        )
        connection.execute(
            sa.text(
                "UPDATE source_documents SET candidate_id = :candidate_id WHERE id = :source_id"
            ),
            {
                "candidate_id": _candidate_id(topic_id, normalized_url),
                "source_id": canonical["id"],
            },
        )
        for duplicate in candidates:
            if duplicate["id"] == canonical["id"]:
                continue
            connection.execute(
                sa.text(
                    "UPDATE source_documents "
                    "SET candidate_id = NULL, "
                    "duplicate_of_source_document_id = :canonical_id "
                    "WHERE id = :duplicate_id"
                ),
                {
                    "canonical_id": canonical["id"],
                    "duplicate_id": duplicate["id"],
                },
            )

    op.create_unique_constraint(
        "uq_source_documents_user_candidate",
        "source_documents",
        ["user_id", "candidate_id"],
    )
    op.create_index(
        "ix_source_documents_duplicate_of",
        "source_documents",
        ["duplicate_of_source_document_id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE source_documents "
            "SET candidate_id = NULL "
            "WHERE input_type = 'web_url' AND "
            "(candidate_id IS NOT NULL OR duplicate_of_source_document_id IS NOT NULL)"
        )
    )
    op.drop_constraint(
        "fk_source_documents_duplicate_of", "source_documents", type_="foreignkey"
    )
    op.drop_index("ix_source_documents_duplicate_of", table_name="source_documents")
    op.drop_constraint(
        "uq_source_documents_user_candidate", "source_documents", type_="unique"
    )
    op.drop_column("source_documents", "duplicate_of_source_document_id")