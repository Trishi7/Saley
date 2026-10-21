// One story of Signals (R5 T15). The title, the "why" line and the outlets are DATA from news
// feeds: they render as text only, never as markup. The story link is an external Button
// (https only, from the API; new tab, rel noopener noreferrer); nothing on the page fetches it.
// Hooks: `[data-row][data-story]` and `data-cat` (the topic name, or "" for Other).
import { ExternalButton } from "@/components/saley/Links";
import { Tag } from "@/components/saley/Tag";
import { fmt, W } from "@/words";
import { categoryOf, isImportant, outletsText } from "./signal-rules";
import type { LinkedItem, SignalItem, StoryItem } from "./types";

function isLinked(item: SignalItem): item is LinkedItem {
  return "story_id" in item;
}

function Score({ label, n }: { label: string; n: number | null }) {
  return <span className="tabular">{fmt(label, { n: n ?? "—" })}</span>;
}

export function SignalRow({ item }: { item: SignalItem }) {
  const linked = isLinked(item);
  const topic = linked ? undefined : (item as StoryItem).categories[0];
  const cat = categoryOf(topic);
  const ours = !linked && (item as StoryItem).ours;
  const from = outletsText(item.outlets, item.source);
  return (
    <li
      data-row
      data-story={item.id}
      data-cat={topic ?? ""}
      className="flex flex-col gap-2 px-4 py-3.5"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {cat && (
          <Tag tag={cat.tag} tone={cat.tone}>
            {cat.label}
          </Tag>
        )}
        {ours && (
          <Tag tag="ours" tone="primary">
            {W.lgOurs}
          </Tag>
        )}
        {isImportant(item.importance) && (
          <Tag tag="important" tone="warn">
            {W.lgImportant}
          </Tag>
        )}
        {linked && item.verify && (
          <Tag tag="verify" tone="warn">
            {W.lgVerify}
          </Tag>
        )}
        <span className="text-muted-foreground tabular ml-auto text-xs">{item.published_label}</span>
      </div>
      <p className="text-sm leading-snug font-medium text-pretty">{item.title}</p>
      {linked && (
        <p className="text-sm text-pretty" data-field="entity">
          {fmt(W.linkedTo, {
            e: [item.entity, item.account && item.account !== item.entity ? item.account : null]
              .filter(Boolean)
              .join(" · ") || "—",
          })}
        </p>
      )}
      {linked && (
        <p className="text-muted-foreground text-sm text-pretty" data-field="hint">
          {fmt(W.hintLine, { h: item.action_hint })}
        </p>
      )}
      {item.why && <p className="text-muted-foreground line-clamp-2 text-sm text-pretty">{item.why}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground flex min-w-0 flex-wrap gap-x-3 gap-y-0.5 text-xs">
          {from && <span className="min-w-0 truncate">{from}</span>}
          <Score label={W.impLabel} n={item.importance} />
          <Score label={W.impactLabel} n={item.impact} />
          {linked && item.state !== "new" && <span>{fmt(W.stateLabel, { s: item.state })}</span>}
        </p>
        {item.href && (
          <ExternalButton href={item.href} label={fmt(W.openStoryAria, { t: item.title })}>
            {W.openStory}
          </ExternalButton>
        )}
      </div>
    </li>
  );
}
