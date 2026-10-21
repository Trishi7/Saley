// The /api/commitments answer (src/saley/web/schemas.py CommitmentsOut) and the Done answer.
import type { Tone } from "@/components/saley/tones";
import { W } from "@/words";

export type Commitment = {
  id: number;
  owner_short: string;
  owner_name: string;
  text: string;
  due: string | null;
  due_label: string | null;
  source: string;
  state: string;
  reminders_sent: number;
  in_pack: boolean;
  /** The signed-in person's own Done still inside its window (R5 T16a review). */
  undo_id: number | null;
  undo_until: string | null;
};

export type CommitmentsData = { items: Commitment[]; total: number; last_computed_label: string };

/** POST /api/commitment/{id}/done (schemas.CommitmentDoneOut). */
export type DoneAnswer = {
  ok: boolean;
  commitment_id: number;
  already: boolean;
  write_id: number | null;
  undo_until: string | null;
};

export const STATES = ["open", "done", "dropped"] as const;
export type CommitState = (typeof STATES)[number];

export const STATE_TAG: Record<CommitState, { name: string; tone: Tone }> = {
  open: { name: W.lgOpen, tone: "info" },
  done: { name: W.lgDone, tone: "good" },
  dropped: { name: W.lgDropped, tone: "neutral" },
};
