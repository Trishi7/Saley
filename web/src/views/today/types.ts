// The /api/today answer as the Today page reads it (src/saley/web/schemas.py TodayOut).
// Only the fields this page shows are typed; the server drops the rest (extra="ignore").
import type { TileData } from "@/components/saley/Tile";
import type { FunnelStep } from "@/components/saley/Funnel";
import type { ViewLink } from "@/lib/format";

export type TaskActionBody = {
  action: "write" | "email_write" | "task_done";
  target_ids: number[];
  payload?: Record<string, unknown> | null;
};

export type Task = {
  key: string;
  kind: string;
  title: string;
  why: string | null;
  contact_id: number | null;
  name: string | null;
  company: string | null;
  due: string | null;
  due_label: string | null;
  action: { endpoint: string; body: TaskActionBody | null } | null;
  undo_id: number | null;
  undo_ids: number[];
  undo_until: string | null;
  done: boolean;
  input_role: string | null;
  soon: string | null;
  link: ViewLink | null;
};

export type MyTasks = {
  person: string | null;
  items: Task[];
  total: number;
  offset: number;
  more: number;
  more_label: string | null;
  empty: string;
};

export type LookKind = "news" | "event" | "blocker" | "waiting";
export type LookCard = { kind: LookKind; title: string; why: string; link: ViewLink };

export type Suggestion = {
  kind: string;
  id: number;
  subject: string;
  company: string | null;
  link: ViewLink | null;
  can_find_people: boolean;
  line: string | null;
};

export type TodayData = {
  greeting: string;
  date_label: string;
  tiles: TileData[];
  funnel: FunnelStep[];
  blockers_line: { count: number | null; label: string; link: ViewLink } | null;
  my_tasks: MyTasks;
  needs_look: { cards: LookCard[]; soon: string | null; empty: string };
  saley_suggests: { items: Suggestion[]; empty: string };
  last_computed_label: string;
};

/** POST /api/act answer (schemas.ActOut). */
export type ActAnswer = {
  ok: boolean;
  already: boolean;
  write_id: number;
  undo_until: string | null;
  label: string;
  items: { write_id: number; already: boolean; undo_until: string | null }[];
};

/** POST /api/ask answer (schemas.AskOut). */
export type AskAnswer = { text: string; known: boolean; shown: number; more: number; left_out: number };
