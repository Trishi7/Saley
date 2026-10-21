// The /api/goals answer as the Q4 goals page reads it (src/saley/web/schemas.py GoalsOut).
// Only the fields this page shows are typed; the server drops the rest (extra="ignore").
import type { Progress } from "@/components/saley/Ring";

export type GoalPerson = {
  id: number;
  name: string;
  company: string | null;
  stage: string;
  /** One line: the stored next step, else the stage (goal_model, R5 T5). */
  note: string;
};

export type GoalBlocker = {
  item: string;
  short: string;
  owner: string | null;
  due: string | null;
  late_by_days: number | null;
  status: string | null;
};

export type Goal = {
  id: number;
  code: string;
  text: string;
  value_text: string | null;
  need: number | null;
  due: string | null;
  due_label: string;
  days_left: number | null;
  active_count: number;
  active: GoalPerson[];
  won: number;
  progress: Progress | null;
  blockers: GoalBlocker[];
  blocker_count: number;
  rag: string;
  why: string;
};

export type GoalsData = {
  items: Goal[];
  total: number;
  last_computed_label: string;
};
