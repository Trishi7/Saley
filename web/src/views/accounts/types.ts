// The /api/accounts and /api/accounts/{id} answers as the Accounts page and the company
// drawer read them (src/saley/web/schemas.py AccountsOut, AccountOut).
import type { TimelineMonth } from "@/components/saley/TouchTimeline";

export type Account = {
  id: number;
  name: string;
  industry: string | null;
  geo: string | null;
  funding: string | null;
  in_master: boolean;
  goal_codes: string[];
  people: number;
  p1_count: number;
  threads: number;
  coverage_pct: number | null;
  /** {stage: people} (accounts_model, R5 T6). */
  stage_counts: Record<string, number>;
  /** "13 people · 9 P1": shown as it is. */
  people_label: string;
  /** Qualified, Engaged, Single-threaded. */
  tags: string[];
};

export type Industry = { industry: string; people: number; contacted: number; pct: number | null };
export type TagLegend = { tag: string; meaning: string; source: string };

export type AccountsData = {
  items: Account[];
  total: number;
  filter: string;
  with_people: number;
  in_master: number;
  industries: Industry[];
  tags_legend: TagLegend[];
  last_computed_label: string;
};

export type AccountPerson = { id: number; name: string; title: string | null; stage: string };
export type StageEntry = { stage: string; from_at: string; reason: string | null; name?: string | null };

export type AccountDetail = Account & {
  people_list: AccountPerson[];
  timeline: TimelineMonth[];
  stage_history: StageEntry[];
};

export type AccountData = { item: AccountDetail };
