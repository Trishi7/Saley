// Commitments (R5 T16a; docs/08 view 10): every promise from Discord or a meeting.
// Numbers first (open, done, dropped for the owner picked), then the filters (state, default
// open; owner), then the list, 10 a page: promise, owner, due, source, state, reminders sent.
// Mark done is the owner's only (D155, CommitmentRow). Filters live in the hash
// (`#commit?state=done&owner=sid`). Hooks: `#commit-table tbody tr[data-id]`,
// `[data-act="commitment-done"]`, `[data-stat]`, `[data-filter]`.
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { FilterBar } from "@/components/saley/FilterBar";
import { Pager, PAGE_SIZE } from "@/components/saley/Pager";
import { Stat } from "@/components/saley/Stat";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { useMe } from "@/hooks/use-me";
import { useTicks } from "@/hooks/use-press";
import { useRoute } from "@/hooks/use-route";
import { go } from "@/lib/route";
import { rowTick, sumKnown } from "@/lib/team-rules";
import { istToday } from "@/views/today/task-rules";
import { fmt, W } from "@/words";
import { CommitmentRow } from "./CommitmentRow";
import { STATE_TAG, STATES, type CommitState, type CommitmentsData } from "./types";

const ALL = "all";
const DEFAULT_STATE = "open";
const SHORT = /^[a-z]+$/; // team.ShortName: anything else would be a 422

function query(params: Record<string, string | number | null>): string {
  return Object.entries(params)
    .filter(([, v]) => v !== null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join("&");
}

function useCount(state: CommitState, owner: string | null) {
  return useApi<CommitmentsData>(`/api/commitments?${query({ state, owner, limit: 1 })}`);
}

export function CommitmentsPage({ view }: { view: ViewDef }) {
  const { me } = useMe();
  const params = useRoute().params;
  const asked = params.state ?? "";
  const state: string = ([...STATES, ALL] as string[]).includes(asked) ? asked : DEFAULT_STATE;
  const owner = params.owner && SHORT.test(params.owner) ? params.owner : null;
  const [offset, setOffset] = useOffset(`${state}|${owner ?? ""}`);
  const list = useApi<CommitmentsData>(
    `/api/commitments?${query({ state, owner, limit: PAGE_SIZE, offset })}`,
  );
  const kept = useKeptPage(list.data, offset);
  const counts = {
    open: useCount("open", owner),
    done: useCount("done", owner),
    dropped: useCount("dropped", owner),
  };
  const { ticks, now, setTick } = useTicks();
  const reload = () => {
    list.reload();
    Object.values(counts).forEach((c) => c.reload());
  };
  const n = (s: CommitState) => counts[s].data?.total ?? null;
  const move = (next: { state?: string; owner?: string | null }) => {
    const s = next.state ?? state;
    const o = next.owner === undefined ? owner : next.owner;
    const hash: Record<string, string> = {};
    if (s !== DEFAULT_STATE) hash.state = s;
    if (o) hash.owner = o;
    go("commit", Object.keys(hash).length ? hash : undefined);
  };
  const stateOptions = [
    ...STATES.map((s) => ({
      id: s,
      label: fmt(W.tabCount, { label: STATE_TAG[s].name, n: n(s) ?? "—" }),
    })),
    { id: ALL, label: fmt(W.tabCount, { label: W.tabAll, n: sumKnown(STATES.map(n)) ?? "—" }) },
  ];
  const ownerOptions = [
    { id: ALL, label: W.fAll },
    ...(me?.people ?? []).map((p) => ({ id: p.short, label: p.name })),
  ];
  const error = list.error ?? Object.values(counts).find((c) => c.error)?.error ?? null;
  const page = kept?.page;
  const today = istToday(now);

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatCommit} error={error} onRetry={reload} />}
      {!page && list.loading && <LoadingLine />}
      {page && (
        <div data-content className="flex flex-col gap-5" aria-busy={list.loading || undefined}>
          <div id="tiles" className="grid grid-cols-2 gap-3 sm:grid-cols-3 [&>*:last-child:nth-child(odd)]:col-span-2 sm:[&>*:last-child:nth-child(odd)]:col-span-1">
            {STATES.map((s) => (
              <Stat key={s} id={s} label={STATE_TAG[s].name} value={n(s)} />
            ))}
          </div>
          <div className="flex flex-col gap-2">
            <FilterBar
              options={stateOptions}
              current={state}
              onPick={(id) => move({ state: id })}
              label={W.colState}
            />
            <FilterBar
              options={ownerOptions}
              current={owner ?? ALL}
              onPick={(id) => move({ owner: id === ALL ? null : id })}
              label={W.ownerLabel}
            />
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-labelledby="commit-title">
            <header className="border-b px-4 py-3">
              <h2 id="commit-title" className="text-base font-semibold">
                {W.navCommit}
              </h2>
              <p className="text-muted-foreground text-xs">{W.cOwnerHint}</p>
            </header>
            <Pager
              total={page.total}
              offset={kept?.offset ?? offset}
              shown={page.items.length}
              onMove={setOffset}
              label={W.navCommit}
              className="pb-3 [&>nav]:px-4"
            >
              {page.items.length === 0 ? (
                <div className="px-4 py-6">
                  <p className="empty text-muted-foreground text-sm">{W.cEmpty}</p>
                </div>
              ) : (
                <Table id="commit-table" className="min-w-[52rem] table-fixed">
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>{W.colPromise}</TableHead>
                      <TableHead className="w-[13%]">{W.colOwner}</TableHead>
                      <TableHead className="w-[13%]">{W.colDue}</TableHead>
                      <TableHead className="w-[13%]">{W.colSource}</TableHead>
                      <TableHead className="w-[14%]">{W.colState}</TableHead>
                      <TableHead className="w-[9%] text-right">{W.colReminders}</TableHead>
                      <TableHead className="w-[15%]">
                        <span className="sr-only">{W.cMarkDone}</span>
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {page.items.map((item) => (
                      <CommitmentRow
                        key={item.id}
                        item={item}
                        tick={rowTick(item, ticks[String(item.id)])}
                        now={now}
                        today={today}
                        onTick={setTick}
                        onChanged={reload}
                      />
                    ))}
                  </TableBody>
                </Table>
              )}
            </Pager>
          </section>
          <p className="text-muted-foreground nums text-xs">
            {fmt(W.lastComputed, { t: page.last_computed_label })}
          </p>
        </div>
      )}
    </ViewPage>
  );
}
