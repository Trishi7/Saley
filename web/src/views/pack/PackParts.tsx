// The blocks of the weekly pack body (R4 D117, R5 T16a). Same hooks as the legacy page, so the
// R4 browser check keeps its places: `#pack-tiles .tile` (`.v` value, `.d` last week),
// `#pack-late`, `#pack-reminders` ("Showing n of total", D186), `#pack-tasks`, `#pack-score`.
// Long lists page by 10 inside the page (the body is already here; no extra call).
import type { ReactNode } from "react";
import { OneLine } from "@/components/saley/Links";
import { Pager } from "@/components/saley/Pager";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useOffset } from "@/hooks/use-kept";
import { useMe } from "@/hooks/use-me";
import { dayLabel, num } from "@/lib/format";
import { pageOf } from "@/lib/team-rules";
import { ScoreTable, Trend } from "@/views/scorecard/ScoreTable";
import { fmt, W } from "@/words";
import type { PackBody, PackNumber } from "./types";

function Block({
  id,
  title,
  hint,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="bg-card flex min-w-0 flex-col rounded-lg border" aria-labelledby={`${id}-t`}>
      <header className="flex flex-wrap items-baseline gap-x-2 border-b px-4 py-3">
        <h2 id={`${id}-t`} className="text-base font-semibold">
          {title}
        </h2>
        {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
      </header>
      {children}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="empty text-muted-foreground px-4 py-6 text-sm">{text}</p>;
}

function Shown({ text }: { text: string }) {
  return <p className="text-muted-foreground nums px-4 pb-3 text-xs">{text}</p>;
}

/** A person's name from the team list; the short name when it is not there. */
function useOwnerName(): (short: string | null) => string {
  const { me } = useMe();
  return (short) => {
    if (!short) return W.packNoOwner;
    return me?.people.find((p) => p.short === short)?.name ?? short;
  };
}

function lastLine(n: PackNumber): string {
  if (n.last_week !== null) return fmt(W.packLastWeek, { v: n.last_week });
  return n.last_week_note || "—";
}

export function TeamTiles({ team }: { team: PackNumber[] }) {
  return (
    <div id="pack-tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {team.map((n) => (
        <div key={n.key} data-key={n.key} data-stat={n.key} className="tile bg-card flex min-w-0 flex-col gap-1 rounded-lg border p-4">
          <span className="l text-muted-foreground text-sm leading-snug">{n.label}</span>
          <span className="flex items-baseline">
            <span
              className={`v tabular text-3xl font-semibold tracking-tight ${n.value === null ? "text-muted-foreground" : ""}`}
            >
              {num(n.value)}
            </span>
            <Trend value={n.value} last={n.last_week} />
          </span>
          <span className="d text-muted-foreground text-xs">{lastLine(n)}</span>
        </div>
      ))}
    </div>
  );
}

export function LateBlock({ body }: { body: PackBody }) {
  const owner = useOwnerName();
  const [offset, setOffset] = useOffset("pack-late");
  const rows = pageOf(body.late, offset);
  return (
    <Block id="pack-late" title={W.packLateTitle}>
      {body.late.length === 0 ? (
        <Empty text={W.packLateNone} />
      ) : (
        <Pager total={body.late.length} offset={offset} shown={rows.length} onMove={setOffset} label={W.packLateTitle} className="pb-3 [&>nav]:px-4">
          <Table className="min-w-[40rem] table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{W.colItem}</TableHead>
                <TableHead className="w-[18%]">{W.colOwner}</TableHead>
                <TableHead className="w-[15%]">{W.colDue}</TableHead>
                <TableHead className="w-[12%] text-right">{W.colLateBy}</TableHead>
                <TableHead className="w-[22%]">{W.colBlocks}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((l, i) => (
                <TableRow key={`${l.item}-${i}`} data-row>
                  <TableCell className="py-2.5 font-medium">
                    <OneLine text={l.item} />
                  </TableCell>
                  <TableCell className="py-2.5">
                    <OneLine text={owner(l.owner)} />
                  </TableCell>
                  <TableCell className="nums py-2.5 text-xs">{dayLabel(l.due)}</TableCell>
                  <TableCell className="tabular py-2.5 text-right">
                    {l.late_by_days === 1 ? W.packLateByOne : fmt(W.packLateBy, { n: l.late_by_days })}
                  </TableCell>
                  <TableCell className="text-muted-foreground py-2.5">
                    <OneLine text={l.blocks.join(", ") || null} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Pager>
      )}
      {body.late_total > body.late.length && (
        <Shown text={fmt(W.packLateShown, { n: body.late.length, m: body.late_total })} />
      )}
    </Block>
  );
}

export function RemindBlock({ body }: { body: PackBody }) {
  const owner = useOwnerName();
  const [offset, setOffset] = useOffset("pack-reminders");
  const list = body.packed_reminders;
  const rows = pageOf(list, offset);
  return (
    <Block id="pack-reminders" title={W.packRemindTitle}>
      {list.length === 0 ? (
        <Empty text={W.packRemindNone} />
      ) : (
        <Pager total={list.length} offset={offset} shown={rows.length} onMove={setOffset} label={W.packRemindTitle} className="pb-3 [&>nav]:px-4">
          <Table className="min-w-[32rem] table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{W.colPromise}</TableHead>
                <TableHead className="w-[22%]">{W.colOwner}</TableHead>
                <TableHead className="w-[20%]">{W.colDue}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r, i) => (
                <TableRow key={`${r.text}-${i}`} data-row>
                  <TableCell className="py-2.5">
                    <OneLine text={r.text} />
                  </TableCell>
                  <TableCell className="py-2.5">
                    <OneLine text={owner(r.owner)} />
                  </TableCell>
                  <TableCell className="nums py-2.5 text-xs">{dayLabel(r.due)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Pager>
      )}
      <Shown text={fmt(W.packRemindShown, { n: list.length, m: body.packed_total })} />
    </Block>
  );
}

export function TasksBlock({ body }: { body: PackBody }) {
  const owner = useOwnerName();
  return (
    <Block id="pack-tasks" title={W.packTasksTitle}>
      <div className="grid gap-4 p-4 sm:grid-cols-2">
        {body.tasks.map((p) => (
          <div key={p.person} className="flex min-w-0 flex-col gap-1.5">
            <p className="text-sm">
              <span className="font-medium">{owner(p.person)}</span>
              <span className="text-muted-foreground nums">
                {" · "}
                {p.total === 1 ? W.packTasksOne : fmt(W.packTasksCount, { n: p.total })}
              </span>
            </p>
            <ul className="text-sm">
              {p.items.length === 0 && <li className="empty text-muted-foreground">{W.packTasksNone}</li>}
              {p.items.map((t, i) => (
                <li key={`${t.title}-${i}`} className="flex min-w-0 items-baseline gap-2 py-0.5">
                  <span className="min-w-0 flex-1">
                    <OneLine text={t.title} />
                  </span>
                  {t.due_label && <span className="text-muted-foreground nums shrink-0 text-xs">{t.due_label}</span>}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Block>
  );
}

export function ScoreBlock({ body }: { body: PackBody }) {
  const [offset, setOffset] = useOffset("pack-score");
  const rows = pageOf(body.scorecard, offset);
  return (
    <Block id="pack-score" title={W.packScoreTitle}>
      {body.scorecard.length === 0 ? (
        <Empty text={W.packScoreNone} />
      ) : (
        <Pager total={body.scorecard.length} offset={offset} shown={rows.length} onMove={setOffset} label={W.packScoreTitle} className="pb-3 [&>nav]:px-4">
          <ScoreTable id="pack-score-table" rows={rows} />
        </Pager>
      )}
    </Block>
  );
}
