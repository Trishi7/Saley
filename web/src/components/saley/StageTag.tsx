// A stage badge in its named stage colour (D201, D203): the dot wears the same token as the
// legend swatch, the text stays ink (dataviz rule). The name is the legend's name ("Talking
// now" for stage Engaged), so the badge and the legend read the same. An unknown stage keeps
// its sheet name and the plain "Stage" legend entry; no stage at all shows "—".
import { Tag } from "./Tag";
import { stageTone } from "./tones";
import { STAGES } from "./views";

export function StageTag({ stage, className }: { stage: string | null; className?: string }) {
  const tone = stageTone(stage);
  const entry = STAGES.find((s) => s.tag === tone);
  return (
    <Tag tag={entry ? entry.tag : "stage"} tone={tone} className={className}>
      {entry?.name ?? (stage || "—")}
    </Tag>
  );
}
