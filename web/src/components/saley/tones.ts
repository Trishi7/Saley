// Colour roles for tags, legend swatches and chart marks (D197). One map, so a tag on the page
// and its swatch in the legend always share the colour. Classes are written out in full:
// Tailwind finds class names by scanning the source, never by building strings.

export type Tone =
  | "good"
  | "warn"
  | "bad"
  | "info"
  | "soon"
  | "neutral"
  | "primary"
  | "stage-listed"
  | "stage-contacted"
  | "stage-connected"
  | "stage-talking"
  | "stage-met"
  | "stage-qualified"
  | "stage-won"
  | "stage-parked"
  | "stage-lost"
  | "touch-li"
  | "touch-email"
  | "touch-meet"
  | "touch-other"
  | "cat-1"
  | "cat-2"
  | "cat-3"
  | "cat-4"
  | "cat-5"
  | "cat-6";

/** The CSS colour of a tone, for a swatch, a dot or a bar segment. */
export function toneColour(tone: Tone): string {
  return `var(--${tone === "primary" ? "primary" : tone})`;
}

/** Badge colours: ink text on a soft wash for state tones; data tones use a neutral badge
 *  with a coloured dot, so text never wears a series colour (dataviz rule). */
export const BADGE: Partial<Record<Tone, string>> = {
  good: "bg-good-soft text-good border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  bad: "bg-bad-soft text-bad border-transparent",
  info: "bg-info-soft text-info border-transparent",
  soon: "bg-soon-soft text-soon border-transparent",
  neutral: "bg-neutral-soft text-neutral border-transparent",
  primary: "bg-secondary text-secondary-foreground border-transparent",
};

/** Pipeline order (docs/08 stages). Parked and Lost are off the path. */
const STAGE_ORDER = ["Listed", "Contacted", "Connected", "Engaged", "Meeting", "Qualified", "Won"];

/** D201: one named colour per stage, the same on every page. Engaged is "Talking now" and
 *  Meeting is "Met" on the Today funnel, so they share those colours. */
const STAGE_TONES: Record<string, Tone> = {
  Listed: "stage-listed",
  Contacted: "stage-contacted",
  Connected: "stage-connected",
  Engaged: "stage-talking",
  Meeting: "stage-met",
  Qualified: "stage-qualified",
  Won: "stage-won",
  Parked: "stage-parked",
  Lost: "stage-lost",
};

export function stageTone(stage: string | null | undefined): Tone {
  return STAGE_TONES[stage ?? ""] ?? "neutral";
}

/** The Today funnel bars (today_model.FUNNEL_STEPS keys) in the same stage colours (D201). */
export const FUNNEL_TONES: Record<string, Tone> = {
  listed: "stage-listed",
  contacted: "stage-contacted",
  connected: "stage-connected",
  talking: "stage-talking",
  met: "stage-met",
  won: "stage-won",
};

/** Stages in bar order: the path first, then Parked and Lost, then any unknown name. */
export function stageRank(stage: string): number {
  const at = STAGE_ORDER.indexOf(stage);
  if (at >= 0) return at;
  if (stage === "Parked") return STAGE_ORDER.length;
  if (stage === "Lost") return STAGE_ORDER.length + 1;
  return STAGE_ORDER.length + 2;
}
