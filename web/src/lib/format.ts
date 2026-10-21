// Small display helpers shared by the views (R5 T12). Pure functions: no DOM, no fetch.
// Numbers: a value that is not known is "—", never 0 (docs/08 shell, R1.1). Dates: IST days
// as "Fri 17 Oct" (docs/08 shared rules); the API already sends IST dates.

const DASH = "—";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** A count or a dash. Zero is a real zero; null or undefined is "not known". */
export function num(value: number | string | null | undefined): string {
  return value === null || value === undefined || value === "" ? DASH : String(value);
}

/** "2026-10-17" or an ISO time -> "Sat 17 Oct". A missing or broken date gives the dash. */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return DASH;
  const date = new Date(Date.UTC(y, m - 1, d));
  return `${DAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}`;
}

/** The API's link object ({view, filter}) as a hash link of this app. */
export type ViewLink = { view: string; filter?: string | null };

export function hrefFor(link: ViewLink): string {
  return `#${link.view}${link.filter ? `?filter=${encodeURIComponent(link.filter)}` : ""}`;
}

/** Percent for a bar segment: never NaN, never below 0. */
export function share(part: number, whole: number): number {
  return whole > 0 ? Math.max(0, (part / whole) * 100) : 0;
}
