// The /api/decisions answer (src/saley/web/schemas.py DecisionsOut) and the tags of a row.
import type { Tone } from "@/components/saley/tones";
import { W } from "@/words";

export type Decision = {
  id: number;
  kind: string;
  label: string | null;
  text: string | null;
  what: string | null;
  value: string | null;
  state: string;
  undo_state: string;
  undo_until: string | null;
  at: string;
  surface: string | null;
  user_short: string | null;
  user_name: string | null;
  person: string | null;
  confirmed_by_name: string | null;
  can_confirm: boolean;
  can_undo: boolean;
  sync_state: string | null;
  sync_reason: string | null;
};

export type DecisionsData = { items: Decision[]; total: number; last_computed_label: string };

type TagDef = { tag: string; name: string; tone: Tone };

export const STATE_TAGS: Record<string, TagDef> = {
  proposed: { tag: "proposed", name: W.lgProposed, tone: "warn" },
  confirmed: { tag: "confirmed", name: W.lgConfirmed, tone: "good" },
  active: { tag: "active", name: W.lgActive, tone: "primary" },
};

export const UNDO_TAGS: Record<string, TagDef> = {
  open: { tag: "undo-open", name: W.undoOpen, tone: "info" },
  undone: { tag: "undone", name: W.lgUndone, tone: "neutral" },
  closed: { tag: "undo-closed", name: W.undoClosed, tone: "neutral" },
  undo_failed: { tag: "undo-failed", name: W.undoFailed, tone: "bad" },
};

export const SYNC_TAGS: Record<string, TagDef> = {
  pending: { tag: "sync-pending", name: W.syncPending, tone: "warn" },
  held: { tag: "sync-held", name: W.syncHeld, tone: "warn" },
  done: { tag: "sync-done", name: W.syncDone, tone: "good" },
  failed: { tag: "sync-failed", name: W.syncFailed, tone: "bad" },
  cancelled: { tag: "sync-cancelled", name: W.syncCancelled, tone: "neutral" },
};

/** Kinds with no undo state to show (legacy team.js decisionRow). */
export const NO_UNDO_KINDS = new Set(["strategy_decision", "dismiss"]);

export const SURFACES = [
  { id: "dashboard", name: W.surfDashboard },
  { id: "chat", name: W.surfChat },
  { id: "card", name: W.surfCard },
];

export const UNDO_FILTERS = [
  { id: "open", name: W.undoOpen },
  { id: "undone", name: W.lgUndone },
  { id: "undo_failed", name: W.undoFailed },
  { id: "closed", name: W.undoClosed },
];

export const STATE_FILTERS = [
  { id: "proposed", name: W.lgProposed },
  { id: "confirmed", name: W.lgConfirmed },
  { id: "active", name: W.lgActive },
];
