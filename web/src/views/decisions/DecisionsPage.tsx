// Decisions (R5 T16a; docs/08 view 11): every choice the team made, with who, when and where.
// Numbers first (decisions that match, wait for a confirm, can undo now), then the filters
// (person, where, undo, state: native selects with the legacy ids `#dec-person`,
// `#dec-surface`, `#dec-undo`, plus `#dec-state`), then the log, 10 a page.
// Hooks: `#dec-table tbody tr[data-id]`, `[data-act="confirm"]`, `[data-act="undo"]`.
import { useState } from "react";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { Pager, PAGE_SIZE } from "@/components/saley/Pager";
import { Stat } from "@/components/saley/Stat";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { useMe } from "@/hooks/use-me";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { DecisionRow } from "./DecisionRow";
import { STATE_FILTERS, SURFACES, UNDO_FILTERS, type DecisionsData } from "./types";

type Filters = { person: string; surface: string; undo: string; state: string };
const NONE: Filters = { person: "", surface: "", undo: "", state: "" };

function path(filters: Filters, limit: number, offset = 0): string {
  const parts = Object.entries(filters)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return `/api/decisions?${[...parts, `limit=${limit}`, `offset=${offset}`].join("&")}`;
}

function Select({
  id,
  label,
  value,
  options,
  onPick,
}: {
  id: string;
  label: string;
  value: string;
  options: { id: string; name: string }[];
  onPick: (value: string) => void;
}) {
  return (
    <label htmlFor={id} className="flex min-w-0 flex-col gap-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(e) => onPick(e.target.value)}
        className={cn(
          "border-input bg-background h-10 rounded-md border px-2 text-sm sm:h-9",
          "focus-visible:border-ring focus-visible:ring-ring/50 outline-none focus-visible:ring-[3px]",
        )}
      >
        <option value="">{W.fAny}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  );
}

export function DecisionsPage({ view }: { view: ViewDef }) {
  const { me } = useMe();
  const [filters, setFilters] = useState<Filters>(NONE);
  const key = Object.values(filters).join("|");
  const [offset, setOffset] = useOffset(key);
  const list = useApi<DecisionsData>(path(filters, PAGE_SIZE, offset));
  const proposed = useApi<DecisionsData>(path({ ...filters, state: "proposed" }, 1));
  const canUndo = useApi<DecisionsData>(path({ ...filters, undo: "open" }, 1));
  const kept = useKeptPage(list.data, offset);
  const page = kept?.page;
  const reload = () => {
    list.reload();
    proposed.reload();
    canUndo.reload();
  };
  const pick = (name: keyof Filters) => (value: string) => setFilters((f) => ({ ...f, [name]: value }));
  const people = (me?.people ?? []).map((p) => ({ id: p.short, name: p.name }));
  const error = list.error ?? proposed.error ?? canUndo.error;

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatDecisions} error={error} onRetry={reload} />}
      {!page && list.loading && <LoadingLine />}
      {page && (
        <div data-content className="flex flex-col gap-5" aria-busy={list.loading || undefined}>
          <div id="tiles" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat id="decisions" label={W.decTotal} value={page.total} />
            <Stat id="proposed" label={W.decProposed} value={proposed.data?.total ?? null} />
            <Stat id="can-undo" label={W.decCanUndo} value={canUndo.data?.total ?? null} />
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Select id="dec-person" label={W.personLabel} value={filters.person} options={people} onPick={pick("person")} />
            <Select id="dec-surface" label={W.dWhere} value={filters.surface} options={SURFACES} onPick={pick("surface")} />
            <Select id="dec-undo" label={W.undoLabel} value={filters.undo} options={UNDO_FILTERS} onPick={pick("undo")} />
            <Select id="dec-state" label={W.colState} value={filters.state} options={STATE_FILTERS} onPick={pick("state")} />
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-label={W.navDecisions}>
            <Pager
              total={page.total}
              offset={kept?.offset ?? offset}
              shown={page.items.length}
              onMove={setOffset}
              label={W.navDecisions}
              className="pb-3 [&>nav]:px-4"
            >
              {page.items.length === 0 ? (
                <div className="px-4 py-6">
                  <p className="empty text-muted-foreground text-sm">{W.dEmpty}</p>
                </div>
              ) : (
                <Table id="dec-table" className="min-w-[56rem] table-fixed">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead className="w-[6%]">{W.colId}</TableHead>
                      <TableHead className="w-[14%]">{W.dWhen}</TableHead>
                      <TableHead className="w-[12%]">{W.dWho}</TableHead>
                      <TableHead>{W.dWhat}</TableHead>
                      <TableHead className="w-[22%]">{W.colState}</TableHead>
                      <TableHead className="w-[16%]">
                        <span className="sr-only">{W.confirmBtn}</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {page.items.map((d) => (
                      <DecisionRow key={d.id} d={d} onChanged={reload} />
                    ))}
                  </TableBody>
                </Table>
              )}
            </Pager>
          </section>
          <p className="text-muted-foreground tabular text-xs">
            {fmt(W.lastComputed, { t: page.last_computed_label })}
          </p>
        </div>
      )}
    </ViewPage>
  );
}
