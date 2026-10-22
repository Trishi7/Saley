// The Scorecard table, per industry (docs/08 view 13), used by the Scorecard page and the
// weekly pack. "Last week" is the same industry from the snapshot 7 days back (D118): no
// snapshot shows "—" with the server's note, never 0. Contacted, meetings and deals carry an
// Up or Down tag against last week (legend). Hook: the caller's table id, `tbody tr[data-row]`.
import { OneLine } from "@/components/saley/Links";
import { Tag } from "@/components/saley/Tag";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { num } from "@/lib/format";
import { trend } from "@/lib/team-rules";
import { fmt, W } from "@/words";

export type ScoreLastWeek = {
  people: number | null;
  contacted: number | null;
  connect_pct: number | null;
  reply_pct: number | null;
  meetings: number | null;
  deals: number | null;
};

export type ScoreRow = {
  industry: string;
  people: number;
  contacted: number;
  connect_pct: number | null;
  reply_pct: number | null;
  reply_note: string;
  meetings: number;
  deals: number;
  last_week: ScoreLastWeek | null;
  last_week_note: string | null;
};

export function Trend({ value, last }: { value: number | null; last: number | null | undefined }) {
  const way = trend(value, last);
  if (!way) return null;
  return (
    <Tag tag={way} tone={way === "up" ? "good" : "warn"} className="ml-1.5 px-1.5 py-0 text-[11px]">
      {way === "up" ? W.lgUp : W.lgDown}
    </Tag>
  );
}

function LastWeek({ row }: { row: ScoreRow }) {
  const lw = row.last_week;
  if (!lw) {
    return (
      <span className="text-muted-foreground">
        —{row.last_week_note && <span className="ml-1 text-xs">{row.last_week_note}</span>}
      </span>
    );
  }
  return (
    <span className="text-muted-foreground nums text-xs">
      {fmt(W.scLastWeekLine, { c: num(lw.contacted), m: num(lw.meetings), d: num(lw.deals) })}
    </span>
  );
}

export function ScoreTable({ id, rows }: { id: string; rows: ScoreRow[] }) {
  return (
    <Table id={id} className="min-w-[54rem] table-fixed">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[18%]">{W.colIndustry}</TableHead>
          <TableHead className="w-[8%] text-right">{W.colPeople}</TableHead>
          <TableHead className="w-[12%] text-right">{W.colContacted}</TableHead>
          <TableHead className="w-[9%] text-right">{W.colConnectPct}</TableHead>
          <TableHead className="w-[8%] text-right">{W.colReplyPct}</TableHead>
          <TableHead className="w-[11%] text-right">{W.colMeetings}</TableHead>
          <TableHead className="w-[10%] text-right">{W.colDeals}</TableHead>
          <TableHead>{W.scLastWeek}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.industry} data-row>
            <TableCell className="py-2.5 font-medium">
              <OneLine text={r.industry} />
            </TableCell>
            <TableCell className="tabular py-2.5 text-right">{r.people}</TableCell>
            <TableCell className="tabular py-2.5 text-right">
              {r.contacted}
              <Trend value={r.contacted} last={r.last_week?.contacted} />
            </TableCell>
            <TableCell className="tabular py-2.5 text-right">{num(r.connect_pct)}</TableCell>
            <TableCell className="tabular py-2.5 text-right" title={r.reply_pct == null ? r.reply_note : undefined}>
              {num(r.reply_pct)}
            </TableCell>
            <TableCell className="tabular py-2.5 text-right">
              {r.meetings}
              <Trend value={r.meetings} last={r.last_week?.meetings} />
            </TableCell>
            <TableCell className="tabular py-2.5 text-right">
              {r.deals}
              <Trend value={r.deals} last={r.last_week?.deals} />
            </TableCell>
            <TableCell className="py-2.5">
              <LastWeek row={r} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
