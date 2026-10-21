// /api/events answer (schemas.EventsOut, EventItem; R5 T6 City and Venue, T7 seen tabs).
import type { SeenAnswer } from "@/hooks/use-seen-list";

export type EventItem = {
  id: number;
  name: string;
  /** Bangalore, London or Other (D160): never a guess. */
  city: string;
  /** The full Location cell, or null when it is blank. */
  venue: string | null;
  date_label: string;
  days_text: string;
  registered: boolean;
  people_in_city: number;
  connected: number;
  p1: number;
  /** An https link from the API, or null. */
  href: string | null;
  seen: boolean;
};

export type EventsData = Omit<SeenAnswer, "items"> & {
  when: "upcoming" | "past";
  items: EventItem[];
  window: { label: string };
};
