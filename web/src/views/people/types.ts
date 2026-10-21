// The /api/people and /api/people/{id} answers as the People page and the person drawer read
// them (src/saley/web/schemas.py PeopleOut, PersonOut).
import type { TimelineMonth } from "@/components/saley/TouchTimeline";
import type { StageEntry } from "@/views/accounts/types";

export type WorkingOn = {
  found: boolean;
  text: string;
  /** The sheet column name, or "note <date>" (people_model, R5 T6). */
  source: string | null;
  date: string | null;
  href: string | null;
};

export type TouchBrief = { type: string; date: string | null };

export type PersonRow = {
  id: number;
  name: string;
  company: string | null;
  account_id: number;
  title: string | null;
  stage: string;
  is_p1: boolean;
  next_step: string | null;
  last_touches: TouchBrief[];
};

export type PeopleCounts = {
  people: number;
  with_notes: number;
  with_paper: number;
  backlog: number;
  p1_waiting: number;
  in_conversation: number;
  reconnect_due: number;
};

export type PeopleData = {
  items: PersonRow[];
  total: number;
  filter: string;
  counts: PeopleCounts;
  last_computed_label: string;
};

/** D31: who follows up. `name` is null when the sheet names no one on the team. */
export type FollowUpBy = { name: string | null; source: string | null; rule: string };
export type Paper = { text: string; href: string };
export type Opportunity = {
  status: string | null;
  deal_status: string | null;
  prob: number | null;
  size_text: string | null;
};

export type PersonDetail = {
  id: number;
  name: string;
  title: string | null;
  account_id: number;
  company: string | null;
  industry: string | null;
  stage: string;
  priority: string | null;
  city: string | null;
  email: string | null;
  email_href: string | null;
  li_href: string | null;
  site_text: string | null;
  site_href: string | null;
  next_step: string | null;
  li_status: string | null;
  notes: string | null;
  working_on: WorkingOn;
  follow_up_by: FollowUpBy | null;
  papers: Paper[];
  papers_label: string;
  timeline: TimelineMonth[];
  stage_history: StageEntry[];
  opportunity: Opportunity | null;
};

export type PersonData = { item: PersonDetail };
