"""R6 migrations: 0025 (the Opportunity topic, D239)."""

from __future__ import annotations

import os

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


SUPERUSER_URL = os.environ.get("SALEY_SUPERUSER_URL", "")  # CI only (.github/workflows/ci.yml)
ADMIN_CHECK_DB = "saley_admin_check"


@pytest.mark.skipif(not SUPERUSER_URL, reason="needs SALEY_SUPERUSER_URL (the CI test job)")
def test_migrations_as_saley_admin_give_the_owner_model() -> None:
    """Q-R6-4 (D256): every migration runs on a clean database as saley_admin, in deploy order
    (grants.sql as superuser, `alembic upgrade head` as saley_admin, grants.sql again), and
    then owners and grants match docs/01 §11 (`saley.qa.db_owners`). The third R6 compose run
    failed here: a migration switched to `saley` and could not reference saley_admin's table."""
    import psycopg
    from sqlalchemy.engine import make_url

    from saley.core.roles import GRANTS_SQL
    from saley.qa.db_owners import problems

    admin_url = make_url(SUPERUSER_URL).set(
        drivername="postgresql+psycopg",
        username="saley_admin",
        password="saley_admin",  # deploy/sql/roles.sql laptop/CI default
        database=ADMIN_CHECK_DB,
    )
    target = make_url(SUPERUSER_URL).set(database=ADMIN_CHECK_DB)
    target_info = target.render_as_string(hide_password=False)
    grants = GRANTS_SQL.read_text(encoding="utf-8")
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as conn:
        conn.execute(f"drop database if exists {ADMIN_CHECK_DB} with (force)")
        conn.execute(f"create database {ADMIN_CHECK_DB} owner saley_admin")
    try:
        with psycopg.connect(target_info, autocommit=True) as conn:
            conn.execute(grants)
        dispose_engines()
        command.upgrade(alembic_config(admin_url.render_as_string(hide_password=False)), "head")
        with psycopg.connect(target_info, autocommit=True) as conn:
            conn.execute(grants)
            head = conn.execute("select version_num from alembic_version").fetchone()
            found = problems(conn)
        assert head == ("0026",)
        assert found == []
    finally:
        dispose_engines()
        with psycopg.connect(SUPERUSER_URL, autocommit=True) as conn:
            conn.execute(f"drop database if exists {ADMIN_CHECK_DB} with (force)")


def test_no_migration_switches_role() -> None:
    """Q-R6-4 (D256): migrations run as saley_admin only and never `set role`."""
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    for path in sorted(versions.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
        body = code.split('"""', 2)[-1]  # the module docstring may tell the history
        for word in ("set local role", "set role", "reset role", "set session authorization"):
            assert word not in body.lower(), f"{path.name}: {word}"
