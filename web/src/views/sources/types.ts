// The /api/health/full answer (src/saley/web/schemas.py HealthOut, the fields this page shows)
// and the "Run news now" state (src/saley/web/admin.py NewsRunState).
import type { Tone } from "@/components/saley/tones";
import { W } from "@/words";

export type Job = {
  job: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  note: string | null;
};

export type NewsStep = { key: string; step: string; n: number | null; href: string; note: string | null };
export type NewsSource = { name: string; problem: string | null; last_status: string | null };
/** One row of `news.sources` (newsfresh.source_row): the per-source counts of the last 24 h. */
export type NewsSourceRow = NewsSource & {
  active: boolean;
  runs_24h: number;
  errors_24h: number;
  seen_24h: number;
  new_24h: number;
};

export type HealthData = {
  writes: { enabled: boolean; message: string | null; open: number; failed: number };
  mail: { message: string };
  notes: { message: string };
  jobs: Job[];
  parity: { metric: string; sheet_value: number | null; view_value: number | null; ok: boolean; note: string | null }[];
  quality: { problem: string; n: number }[];
  missing_industry: number;
  egress: { host: string; calls: number; errors: number }[];
  llm: { calls: number; cached: number };
  llm_cap: number;
  news: {
    label: string;
    steps: NewsStep[];
    failed_sources: NewsSource[];
    sources: NewsSourceRow[];
    watch: { kind: string; text: string }[];
  };
  news_note: string;
  search: {
    message: string;
    engine: string;
    requests_today: number;
    daily_cap: number;
    unavailable_until: string | null;
    last_failure: string | null;
  };
  apollo: { message: string };
  tabs: {
    tab: string;
    title: string | null;
    status: string;
    missing_headers: string[];
    new_headers: string[];
    stale_since: string | null;
    reason: string | null;
  }[];
  last_computed_label: string;
};

export type NewsRunState = {
  latest: {
    id: number;
    requested_by: string;
    requested_at: string;
    state: string;
    note: string | null;
  } | null;
  open: boolean;
  label: string | null;
};

export type NewsRunAnswer = { ok: boolean; request_id: number; label: string };

type TagDef = { tag: string; name: string; tone: Tone };

/** Job and run states, each with its legend entry (views.ts, view "sources"). */
export const STATUS_TAGS: Record<string, TagDef> = {
  ok: { tag: "ok", name: W.lgOk, tone: "good" },
  done: { tag: "ok", name: W.lgOk, tone: "good" },
  failed: { tag: "failed", name: W.lgFailed, tone: "bad" },
  running: { tag: "running", name: W.lgRunning, tone: "info" },
  queued: { tag: "queued", name: W.lgQueued, tone: "info" },
  skipped: { tag: "skipped", name: W.lgSkipped, tone: "neutral" },
};

/** The item steps of the news funnel: each is a part of the one before (D73). */
export const FUNNEL_KEYS = ["fetched", "deduped", "scored", "scored_4", "linked"];
