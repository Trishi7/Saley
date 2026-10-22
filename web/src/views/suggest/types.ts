// /api/suggest/{kind} answer (schemas.SuggestOut, SuggestionItem; R5 T7 seen tabs, T15 line).
import type { SeenAnswer } from "@/hooks/use-seen-list";

export type SuggestKind = "warm" | "best" | "newco" | "res";

export type FormField = { role: string; value: string };

export type SearchHit = {
  id: number;
  title: string;
  href: string;
  engine: string;
  form: { company: string; fields: FormField[]; needs?: string[] };
};

export type Suggestion = {
  kind: SuggestKind;
  id: number;
  contact_id: number | null;
  account_id: number | null;
  subject: string;
  company: string | null;
  title: string | null;
  score: number;
  parts: { code: string; label: string; points: number }[];
  via: string | null;
  evidence: Record<string, unknown>;
  can_find_people: boolean;
  search: { state: string | null; message: string | null; hits: number | null } | null;
  hits: SearchHit[];
  seen: boolean;
  /** "Add Priya Rao (Sarvam) to pipeline — hiring for evals", checked on the server; null when
   *  the check refused it (the card then shows the name and company). */
  line: string | null;
};

export type SuggestData = Omit<SeenAnswer, "items"> & {
  kind: SuggestKind;
  items: Suggestion[];
  window: { label: string };
  capped: boolean;
  capped_note: string | null;
};
