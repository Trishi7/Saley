"""One edition: pull the due sources, clean and store, then score (docs/04 §2, D70).

Why: an edition is the unit of the news day (06:30, 12:00, 17:00, 22:00 IST; arXiv 10:00;
OpenAlex 07:30). It writes one `news_editions` row and one `news_source_runs` row per source,
so the funnel and the source health are counted from rows. A failing source is backed off, never
fatal: the rest of the edition runs. Only the `saley news` process calls this (D70); the gate
and `saley dev` never fetch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import anthropic
import httpx
from sqlalchemy import select

from saley.core import llm
from saley.core.clock import now_ist
from saley.core.db import session_scope
from saley.core.egress import make_client
from saley.core.logging import get_logger
from saley.core.models import Contact, NewsEdition, NewsSource, NewsSourceRun
from saley.core.settings import get_settings
from saley.intel.hints import HintReport, write_hints
from saley.news.clean import NameIndex
from saley.news.fetch import FetchResult, fetch_source
from saley.news.scorer import BACKEND_LLM, BACKEND_RULES, ScoreReport, score_pending
from saley.news.signals import LinkStats, link_scored
from saley.news.sources import (
    FAMILY_ARXIV,
    FAMILY_NEWS,
    FAMILY_OPENALEX,
    SourceOutcome,
    due_sources,
    family_of,
    name_index,
    rebuild_name_lanes,
    record_outcome,
    short_error,
)
from saley.news.store import IngestCounts, ingest_items

log = get_logger("saley.news.editions")

POC_NAME_LIMIT = 20


@dataclass
class SourceReport:
    """What one source did in one edition (printed by `saley news fetch`)."""

    name: str
    family: str
    status: str
    http_status: int | None = None
    error: str | None = None
    counts: IngestCounts = field(default_factory=IngestCounts)

    def line(self) -> str:
        c = self.counts
        base = f"{self.name} · {self.status}"
        if self.http_status:
            base += f" · http {self.http_status}"
        base += (
            f" · seen {c.seen} · new {c.new} · dropped seen {c.dropped_seen}"
            f" gate {c.dropped_gate} copy {c.dropped_copy}"
        )
        return f"{base} · {self.error}" if self.error else base


@dataclass
class EditionReport:
    edition_id: int
    slot: str
    family: str
    sources: list[SourceReport] = field(default_factory=list)
    scored: ScoreReport | None = None
    linked: LinkStats | None = None
    hints: HintReport | None = None
    error: str | None = None  # the step after the fetch that raised, if any

    @property
    def status(self) -> str:
        """ok, partial (some sources failed) or failed (all of them, or a later step)."""
        if self.error or (self.sources and len(self.failed) == len(self.sources)):
            return "failed"
        return "partial" if self.failed else "ok"

    @property
    def failed(self) -> list[SourceReport]:
        return [s for s in self.sources if s.status == "error"]

    def note(self) -> str:
        total = IngestCounts()
        for s in self.sources:
            total.add(s.counts)
        text = (
            f"{len(self.sources)} sources · {len(self.failed)} failed · seen {total.seen}"
            f" · new {total.new} · dropped seen {total.dropped_seen} gate {total.dropped_gate}"
            f" copy {total.dropped_copy}"
        )
        if self.scored:
            text = f"{text} · score: {self.scored.note()}"
        if self.linked:
            text = f"{text} · link: {self.linked.note()}"
        if self.hints:
            text = f"{text} · {self.hints.note()}"
        return text


def _poc_names() -> list[str]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(Contact.name)
                .where(Contact.removed_at.is_(None), Contact.industry.ilike("%academia%"))
                .order_by(Contact.id)
                .limit(POC_NAME_LIMIT)
            )
        )


def fetch_one(
    source_id: int,
    client: httpx.Client,
    *,
    limit: int,
    conditional: bool = True,
    edition_id: int | None = None,
    poc_names: list[str] | None = None,
) -> SourceReport:
    """Fetch one source, store what is new, write the run row and update the source state.

    If storing fails (a database error, a bug), the story rows roll back and the failure is
    still recorded in a fresh session: a run row with status error, the fail streak and the
    back-off (A4). Without that the source would be fetched again at once, every edition.
    """
    settings = get_settings()
    with session_scope() as session:
        source = session.get(NewsSource, source_id)
        if source is None:
            raise LookupError(f"no news source {source_id}")
        names = name_index(session)
        family = family_of(source)
    started = now_ist()
    result = fetch_source(
        client,
        source,
        user_agent=settings.news_user_agent,
        limit=limit,
        conditional=conditional,
        mailto=settings.openalex_mailto,
        poc_names=poc_names or [],
    )
    try:
        return _store_run(source_id, family, result, started, edition_id, names)
    except Exception as exc:
        log.exception("news_store_failed", source_id=source_id)
        failure = SourceOutcome("error", error=short_error(exc))
        return _store_run(source_id, family, FetchResult(failure), started, edition_id, names)


def _store_run(
    source_id: int,
    family: str,
    result: FetchResult,
    started: datetime,
    edition_id: int | None,
    names: NameIndex,
) -> SourceReport:
    settings = get_settings()
    outcome = result.outcome
    with session_scope() as session:
        row = session.get(NewsSource, source_id)
        assert row is not None
        counts = IngestCounts()
        if outcome.status == "ok":
            # The run start is the clock of the whole run (B5): a fetch that crosses midnight
            # must not put its stories on another day than its run row.
            counts = ingest_items(
                session, row, result.items, now=started, names=names, edition_id=edition_id
            )
            counts.seen += result.filtered
            counts.dropped_gate += result.filtered
            if settings.news_raw_log:
                for item in result.items:
                    log.info("news_raw_item", source=row.name, title=item.title, link=item.link)
        record_outcome(row, outcome, now_ist())
        session.add(
            NewsSourceRun(
                source_id=source_id,
                edition_id=edition_id,
                started_at=started,
                finished_at=now_ist(),
                status=outcome.status,
                http_status=outcome.http_status,
                items_seen=counts.seen,
                items_new=counts.new,
                dropped_seen=counts.dropped_seen,
                dropped_gate=counts.dropped_gate,
                dropped_copy=counts.dropped_copy,
                bytes=result.bytes,
                error=outcome.error,
                route_used=row.route,
            )
        )
        name = row.name
    return SourceReport(name, family, outcome.status, outcome.http_status, outcome.error, counts)


def run_edition(
    slot: str,
    family: str = FAMILY_NEWS,
    *,
    client: httpx.Client | None = None,
    score: bool = True,
    limit: int | None = None,
    client_factory: Callable[[], anthropic.Anthropic] = llm.make_anthropic_client,
) -> EditionReport:
    """Run one edition for a family of sources. Never raises for one bad source."""
    settings = get_settings()
    limit = limit or settings.news_items_per_fetch
    with session_scope() as session:
        edition = NewsEdition(slot=slot, family=family)
        session.add(edition)
        session.flush()
        edition_id = edition.id
        if family == FAMILY_NEWS:
            rebuild_name_lanes(session)
        sources = [(s.id, s.name) for s in due_sources(session, family, now_ist())]
    report = EditionReport(edition_id, slot, family)
    owns_client = client is None
    http = client or make_client()
    poc_names = _poc_names() if family == FAMILY_OPENALEX else []
    try:
        for source_id, name in sources:
            try:
                report.sources.append(
                    fetch_one(
                        source_id,
                        http,
                        limit=limit,
                        edition_id=edition_id,
                        poc_names=poc_names,
                    )
                )
            except Exception as exc:  # one source must not stop the edition
                log.exception("news_source_failed", source=name)
                report.sources.append(SourceReport(name, family, "error", error=short_error(exc)))
    finally:
        if owns_client:
            http.close()
    try:
        if score:
            with session_scope() as session:
                report.scored = score_pending(session, now=now_ist(), client_factory=client_factory)
            # D74: link only what is scored, then write the hints (D79). Both are safe to repeat.
            with session_scope() as session:
                report.linked = link_scored(session, now=now_ist())
            with session_scope() as session:
                report.hints = write_hints(session, client_factory=client_factory)
    except Exception as exc:
        report.error = short_error(exc)
        raise
    finally:
        _finish(report)  # the edition row is closed even when a later step raised (A4)
    return report


def _backends(scored: ScoreReport | None) -> str | None:
    """Every backend that scored in this edition, e.g. "llm,rules". Never None for a mix."""
    if scored is None:
        return None
    names = [
        name
        for name, count in ((BACKEND_LLM, scored.scored_llm), (BACKEND_RULES, scored.scored_rules))
        if count
    ]
    return ",".join(names) or None


def _finish(report: EditionReport) -> None:
    scored = report.scored
    with session_scope() as session:
        edition = session.get(NewsEdition, report.edition_id)
        assert edition is not None
        edition.finished_at = now_ist()
        edition.status = report.status
        note = report.note()
        edition.note = (f"{report.error} · {note}" if report.error else note)[:500]
        edition.scorer_calls = scored.calls if scored else 0
        edition.scorer_backend = _backends(scored)


def _pick_probe_sources(session) -> list[int]:  # type: ignore[no-untyped-def]
    """One active source per kind: a Google lane, the arXiv API, the OpenAlex works query."""
    picks: list[int] = []
    rows = list(session.scalars(select(NewsSource).where(NewsSource.active.is_(True))))
    for family, lane in ((FAMILY_NEWS, "broad"), (FAMILY_ARXIV, None), (FAMILY_OPENALEX, "works")):
        pool = [s for s in rows if family_of(s) == family]
        pool.sort(key=lambda s: (lane is not None and s.lane != lane, s.id))
        if pool:
            picks.append(pool[0].id)
    return picks


def probe_once(limit: int, client: httpx.Client | None = None) -> list[SourceReport]:
    """R3-N03: one request per source kind, `limit` items each, stored but not scored.

    The stored ETag is ignored, so a second run still answers with items (not a 304).
    """
    with session_scope() as session:
        picks = _pick_probe_sources(session)
    owns_client = client is None
    http = client or make_client()
    try:
        return [fetch_one(i, http, limit=limit, conditional=False) for i in picks]
    finally:
        if owns_client:
            http.close()
