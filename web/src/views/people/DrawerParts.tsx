// Building blocks shared by the person and company drawers: a titled section and a list of
// label / value facts. A value that is not on file shows "—" (never a guess, never 0).
import type { ReactNode } from "react";
import { StageTag } from "@/components/saley/StageTag";
import { dayLabel } from "@/lib/format";
import { W } from "@/words";

export function DrawerSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{title}</h3>
      {children}
    </section>
  );
}

export type Fact = [label: ReactNode, value: ReactNode];

export function FactList({ facts }: { facts: Fact[] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] gap-x-4 gap-y-2.5 text-sm">
      {facts.map(([label, value], i) => (
        <div key={i} className="contents">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words">
            {value === null || value === undefined || value === "" ? (
              <span className="text-muted-foreground">—</span>
            ) : (
              value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Stage changes, newest first: day, (person), stage badge, the stored reason. */
export function History({
  entries,
}: {
  entries: { stage: string; from_at: string; reason: string | null; name?: string | null }[];
}) {
  if (entries.length === 0) return <p className="text-muted-foreground text-sm">{W.noHistory}</p>;
  return (
    <ol className="flex flex-col gap-1.5 text-sm" data-history>
      {entries.map((e, i) => (
        <li key={`${e.stage}-${e.from_at}-${i}`} className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-muted-foreground tabular w-24 shrink-0 text-xs">
            {dayLabel(e.from_at)}
          </span>
          {e.name && <span className="font-medium">{e.name}</span>}
          <StageTag stage={e.stage} />
          {e.reason && <span className="text-muted-foreground text-xs">{e.reason}</span>}
        </li>
      ))}
    </ol>
  );
}
