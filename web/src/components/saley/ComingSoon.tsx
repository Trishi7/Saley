// "Coming soon" (brief: a badge on every feature that is off; R5-Q03, D164).
// - `ComingSoon` is the badge: `[data-soon="<feature>"]`.
// - `FeatureGate` wraps the place where a feature lives: `[data-feature="<feature>"]`. While the
//   feature is off, the children are inert (no click, no focus) and the badge shows. While
//   /api/me loads, nothing is decided yet, so the children show as they are and no badge shows.
import { ClockIcon } from "lucide-react";
import type { ReactNode } from "react";
import { type Feature, useFeatures } from "@/hooks/use-me";
import { cn } from "@/lib/utils";
import { W } from "@/words";

/** The plain name of each feature, for the badge's accessible name. */
export const FEATURE_NAME: Record<Feature, string> = {
  find_people: W.featFindPeople,
  email_write: W.featEmailWrite,
  speaker_match: W.featSpeakerMatch,
  apollo: W.featApollo,
  mail_replies: W.featMailReplies,
  dms: W.featDms,
};

export function ComingSoon({
  feature,
  withName = false,
  className,
}: {
  feature: Feature;
  /** Show the feature name before "Coming soon" (when no control names it already). */
  withName?: boolean;
  className?: string;
}) {
  return (
    <span
      data-soon={feature}
      data-tag-ref="soon"
      className={cn(
        "bg-soon-soft text-soon inline-flex w-fit shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        className,
      )}
    >
      <ClockIcon aria-hidden className="size-3" />
      {withName && <span>{FEATURE_NAME[feature]}</span>}
      {withName && <span aria-hidden>·</span>}
      <span>{W.soon}</span>
    </span>
  );
}

export function FeatureGate({
  feature,
  children,
  className,
}: {
  feature: Feature;
  children: ReactNode;
  className?: string;
}) {
  const features = useFeatures();
  const off = features !== null && !features[feature];
  return (
    <div data-feature={feature} className={cn("flex flex-wrap items-center gap-2", className)}>
      <div inert={off} aria-disabled={off || undefined} className={cn(off && "opacity-55")}>
        {children}
      </div>
      {off && <ComingSoon feature={feature} />}
    </div>
  );
}

/** The off features of one page, as named badges (until the page's own controls arrive). */
export function SoonStrip({ features: list }: { features: readonly Feature[] }) {
  const features = useFeatures();
  if (features === null) return null;
  const off = list.filter((f) => !features[f]);
  if (off.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {off.map((f) => (
        <span key={f} data-feature={f}>
          <ComingSoon feature={f} withName />
        </span>
      ))}
    </div>
  );
}
