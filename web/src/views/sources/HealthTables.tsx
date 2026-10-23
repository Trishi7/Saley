// The tables of Sources and health (docs/08 view 14): jobs, calls out, sheet tabs, the parity
// check and the data to fix, plus the connection lines. Long tables page by 10 inside the page
// (the answer is already here). Every line is the server's own text (words.py) or a number.
// Hooks (legacy ids kept): `#jobs-table`, `#egress-table`, `#tabs-table`, `#parity-table`,
// `#dq-table`, `#conn-list`, and `#discord-table` (R7, docs/17 §10).
import type { ReactNode } from "react";
import { OneLine } from "@/components/saley/Links";
import { Pager } from "@/components/saley/Pager";
import { Tag } from "@/components/saley/Tag";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useOffset } from "@/hooks/use-kept";
import { dayLabel, num } from "@/lib/format";
import { istTime, pageOf } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { STATUS_TAGS, type HealthData } from "./types";

export function Card({ id, title, children }: { id?: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`${id ?? title}-t`} className="bg-card flex min-w-0 flex-col rounded-lg border">
      <h2 id={`${id ?? title}-t`} className="border-b px-4 py-3 text-base font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function Paged<T>({
  name,
  items,
  label,
  render,
}: {
  name: string;
  items: T[];
  label: string;
  render: (rows: T[]) => ReactNode;
}) {
  const [offset, setOffset] = useOffset(name);
  const rows = pageOf(items, offset);
  return (
    <Pager total={items.length} offset={offset} shown={rows.length} onMove={setOffset} label={label} className="pb-3 [&>nav]:px-4">
      {render(rows)}
    </Pager>
  );
}

function StatusTag({ status }: { status: string }) {
  const tag = STATUS_TAGS[status];
  if (!tag) return <span className="text-muted-foreground">{status}</span>;
  return (
    <Tag tag={tag.tag} tone={tag.tone}>
      {tag.name}
    </Tag>
  );
}

function when(iso: string | null): string {
  return iso ? fmt(W.dateTime, { d: dayLabel(iso), t: istTime(iso) }) : "—";
}

export function JobsTable({ jobs }: { jobs: HealthData["jobs"] }) {
  return (
    <Card id="jobs" title={W.jobsTitle}>
      <Paged
        name="jobs"
        items={jobs}
        label={W.jobsTitle}
        render={(rows) => (
          <Table id="jobs-table" className="min-w-[40rem] table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[26%]">{W.colJob}</TableHead>
                <TableHead className="w-[20%]">{W.colLastRun}</TableHead>
                <TableHead className="w-[14%]">{W.colStatus}</TableHead>
                <TableHead>{W.colNote}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((j) => (
                <TableRow key={j.job} data-row data-job={j.job}>
                  <TableCell className="py-2.5 font-medium">
                    <OneLine text={j.job} />
                  </TableCell>
                  <TableCell className="nums py-2.5 text-xs">{when(j.finished_at ?? j.started_at)}</TableCell>
                  <TableCell className="py-2.5">
                    <StatusTag status={j.status} />
                  </TableCell>
                  <TableCell className="text-muted-foreground py-2.5">
                    <OneLine text={j.note} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      />
      {jobs.length === 0 && <p className="empty text-muted-foreground px-4 py-6 text-sm">{W.noDataYet}</p>}
    </Card>
  );
}

export function EgressTable({ egress }: { egress: HealthData["egress"] }) {
  return (
    <Card id="egress" title={W.egressTitle}>
      <Paged
        name="egress"
        items={egress}
        label={W.egressTitle}
        render={(rows) => (
          <Table id="egress-table" className="table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{W.colHost}</TableHead>
                <TableHead className="w-[22%] text-right">{W.colCalls}</TableHead>
                <TableHead className="w-[22%] text-right">{W.colErrors}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((e) => (
                <TableRow key={e.host} data-row>
                  <TableCell className="py-2">
                    <OneLine text={e.host} />
                  </TableCell>
                  <TableCell className="tabular py-2 text-right">{e.calls}</TableCell>
                  <TableCell className="tabular py-2 text-right">{e.errors}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      />
      {egress.length === 0 && <p className="empty text-muted-foreground px-4 py-6 text-sm">{W.noDataYet}</p>}
    </Card>
  );
}

function tabWhy(t: HealthData["tabs"][number]): string | null {
  if (t.status === "ok") return t.new_headers.length ? fmt(W.newCols, { h: t.new_headers.join(", ") }) : null;
  const parts = [
    t.missing_headers.length ? fmt(W.missingCols, { h: t.missing_headers.join(", ") }) : t.reason,
    t.stale_since ? fmt(W.tabOld, { t: when(t.stale_since) }) : null,
  ];
  return parts.filter(Boolean).join(" · ") || null;
}

export function TabsTable({ tabs }: { tabs: HealthData["tabs"] }) {
  return (
    <Card id="tabs" title={W.tabsTitle}>
      <Table id="tabs-table" className="table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-[30%]">{W.colTab}</TableHead>
            <TableHead className="w-[16%]">{W.colStatus}</TableHead>
            <TableHead>{W.colNote}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tabs.map((t) => (
            <TableRow key={t.tab} data-tab={t.tab}>
              <TableCell className="py-2">
                <OneLine text={t.title || t.tab} />
              </TableCell>
              <TableCell className="py-2">
                {t.status === "ok" ? (
                  <Tag tag="ok" tone="good">
                    {W.lgOk}
                  </Tag>
                ) : (
                  <Tag tag="old" tone="warn">
                    {W.lgOld}
                  </Tag>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground py-2 text-xs">
                <OneLine text={tabWhy(t)} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

/** "stage:Listed" -> "Stage: Listed"; "people_total" -> "people total" (legacy app.js). */
function metricName(metric: string): string {
  const stage = /^stage:(.+)$/.exec(metric);
  return stage ? fmt(W.fStage, { s: stage[1] }) : metric.replace(/_/g, " ");
}

export function ParityTable({ parity }: { parity: HealthData["parity"] }) {
  return (
    <Card id="parity" title={W.parityTitle}>
      <Paged
        name="parity"
        items={parity}
        label={W.parityTitle}
        render={(rows) => (
          <Table id="parity-table" className="table-fixed">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>{W.colMetric}</TableHead>
                <TableHead className="w-[16%] text-right">{W.colSheet}</TableHead>
                <TableHead className="w-[18%] text-right">{W.colView}</TableHead>
                <TableHead className="w-[22%]">{W.colStatus}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => {
                const skipped = p.note === "skipped: tab stale";
                return (
                  <TableRow key={p.metric} data-row>
                    <TableCell className="py-2">
                      <OneLine text={metricName(p.metric)} />
                    </TableCell>
                    <TableCell className="tabular py-2 text-right">{num(p.sheet_value)}</TableCell>
                    <TableCell className="tabular py-2 text-right">{num(p.view_value)}</TableCell>
                    <TableCell className="py-2">
                      {skipped ? (
                        <StatusTag status="skipped" />
                      ) : p.ok ? (
                        <Tag tag="ok" tone="good">
                          {W.parSame}
                        </Tag>
                      ) : (
                        <Tag tag="failed" tone="bad">
                          {W.parDiffers}
                        </Tag>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      />
      {parity.length === 0 && <p className="empty text-muted-foreground px-4 py-6 text-sm">{W.noDataYet}</p>}
    </Card>
  );
}

export function QualityTable({ data }: { data: HealthData }) {
  return (
    <Card id="dq" title={W.qualityTitle}>
      <Table id="dq-table" className="table-fixed">
        <TableBody>
          <TableRow>
            <TableCell className="py-2">{W.noIndustry}</TableCell>
            <TableCell className="tabular w-[22%] py-2 text-right">{data.missing_industry}</TableCell>
          </TableRow>
          {data.quality.map((q) => (
            <TableRow key={q.problem}>
              <TableCell className="py-2">
                <OneLine text={q.problem.replace(/_/g, " ")} />
              </TableCell>
              <TableCell className="tabular py-2 text-right">{q.n}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

export function Connections({ data }: { data: HealthData }) {
  const s = data.search;
  const lines = [
    data.writes.enabled ? W.connWrites : data.writes.message,
    data.mail.message,
    data.notes.message,
    fmt(W.searchStatus, { e: s.engine, n: s.requests_today, c: s.daily_cap }),
    s.unavailable_until ? fmt(W.searchUntil, { t: s.unavailable_until }) : null,
    s.last_failure,
    s.message,
    data.apollo.message,
  ].filter((line): line is string => Boolean(line));
  return (
    <Card id="conn" title={W.connTitle}>
      <ul id="conn-list" className="flex flex-col gap-1.5 px-4 py-3 text-sm">
        {lines.map((line, i) => (
          <li key={`${i}-${line}`}>{line}</li>
        ))}
      </ul>
    </Card>
  );
}

/** R7 (docs/17 §10): cards, presses, card sync and chat memory of today, from v_bot_health.
 *  The same lines print in `saley bot status`. */
export function DiscordTable({ rows }: { rows: HealthData["discord"]["rows"] }) {
  return (
    <Card id="discord" title={W.discordTitle}>
      <Table id="discord-table" className="table-fixed">
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.label}>
              <TableCell className="py-2">{r.label}</TableCell>
              <TableCell className="tabular w-[40%] py-2 text-right">{r.value}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
