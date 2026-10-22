// The /api/pack answer (src/saley/web/schemas.py PackOut): the stored body of one week (D117).
import type { ScoreRow } from "@/views/scorecard/ScoreTable";

export type PackNumber = {
  key: string;
  label: string;
  value: number | null;
  last_week: number | null;
  last_week_note: string | null;
};

export type PackLate = {
  item: string;
  owner: string | null;
  blocks: string[];
  due: string | null;
  late_by_days: number;
};

export type PackReminder = { owner: string; text: string; due: string | null };

export type PackTasks = {
  person: string;
  total: number;
  items: { title: string; due_label: string | null; kind: string }[];
};

export type PackBody = {
  week_start: string;
  last_week_day: string;
  team: PackNumber[];
  scorecard: ScoreRow[];
  late_total: number;
  late: PackLate[];
  packed_total: number;
  packed_reminders: PackReminder[];
  tasks: PackTasks[];
};

// `empty` is true only when no pack exists at all; then the pack fields are null (D229).
// `prev_week` / `next_week` are the Mondays of the neighbour packs (null when there is none).
export type PackData = {
  week_start: string | null;
  built_at: string | null;
  body: PackBody | null;
  prev_week: string | null;
  next_week: string | null;
  empty: boolean;
  last_computed_label: string;
};
