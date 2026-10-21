// /api/signals answer (schemas.SignalsOut, SignalItem, LinkedItem; R5 T7 seen tabs).
import type { SeenAnswer } from "@/hooks/use-seen-list";

type Common = {
  id: number;
  title: string;
  /** An https link from the API (`https_link`), or null. Never built here. */
  href: string | null;
  outlets: string[];
  source: string | null;
  tag: string | null;
  importance: number | null;
  impact: number | null;
  why: string;
  published_label: string;
  seen: boolean;
};

export type StoryItem = Common & {
  categories: string[];
  linked_count: number;
  ours: boolean;
};

export type LinkedItem = Common & {
  story_id: number;
  entity: string | null;
  account: string | null;
  verify: boolean;
  verify_note: string | null;
  action_hint: string;
  state: string;
};

export type SignalItem = StoryItem | LinkedItem;

export type SignalsData = Omit<SeenAnswer, "items"> & {
  tab: "all" | "linked";
  items: SignalItem[];
  window: { label: string };
  last_computed: string | null;
  stale: boolean;
};
