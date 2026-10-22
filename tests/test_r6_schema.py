"""R6 migrations: 0025 (the Opportunity topic, D239)."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saley.cli import alembic_config
from saley.core.db import dispose_engines


def _story(db: Session, n: int, topic: str | None, kind: str = "news") -> None:
    db.execute(
        text(
            "insert into news_stories (title, url, url_hash, fingerprint, topic, kind) "
            "values (:t, :u, :h, :f, :topic, :kind)"
        ),
        {
            "t": f"s{n}",
            "u": f"https://x.test/{n}",
            "h": f"h{n}",
            "f": f"f{n}",
            "topic": topic,
            "kind": kind,
        },
    )


def test_opportunity_is_a_topic_the_check_takes(db: Session) -> None:
    _story(db, 1, "Opportunity")
    db.commit()
    with pytest.raises(IntegrityError):
        _story(db, 2, "Opportunities")
    db.rollback()


def test_0025_clears_news_topics_keeps_papers_and_round_trips(test_db_url: str) -> None:
    """0025 sets the topic of every non-paper story to null, so the backfill tags it again with
    the D239 rules. A paper keeps Research. The downgrade clears Opportunity, then the old check
    holds again."""
    probe = create_engine(test_db_url)
    config = alembic_config(test_db_url)
    try:
        dispose_engines()
        command.downgrade(config, "0024")
        with probe.begin() as conn:
            conn.execute(text("delete from news_stories"))
            for n, topic, kind in (
                (1, "Event", "news"),
                (2, "Other", "news"),
                (3, "Research", "paper"),
            ):
                conn.execute(
                    text(
                        "insert into news_stories (title, url, url_hash, fingerprint, topic, kind) "
                        "values (:t, :u, :h, :f, :topic, :kind)"
                    ),
                    {
                        "t": f"s{n}",
                        "u": f"https://x.test/{n}",
                        "h": f"h{n}",
                        "f": f"f{n}",
                        "topic": topic,
                        "kind": kind,
                    },
                )
        command.upgrade(config, "0025")
        with probe.begin() as conn:
            got = dict(conn.execute(text("select title, topic from news_stories")).all())
            assert got == {"s1": None, "s2": None, "s3": "Research"}
            conn.execute(text("update news_stories set topic = 'Opportunity' where title = 's1'"))
        command.downgrade(config, "0024")
        with probe.begin() as conn:
            assert (
                conn.execute(text("select topic from news_stories where title = 's1'")).scalar_one()
                is None
            )
    finally:
        command.upgrade(config, "head")
        with probe.begin() as conn:
            conn.execute(text("delete from news_stories"))
        probe.dispose()
        dispose_engines()


def test_decision_act_txid_partial_index(db: Session) -> None:
    """0026 (R5 review follow-up): grouped Undo finds an act's cells by index."""
    index = db.execute(
        text("select indexdef from pg_indexes where indexname = 'ix_decision_act_txid'")
    ).scalar_one()
    assert "(act_txid)" in index and "WHERE (act_txid IS NOT NULL)" in index
