// The Inbox (R5 T16a; docs/08 view 9). It holds only: Saley's questions to the team, the
// team's questions to Saley, reminders, and strategy decisions to confirm, plus Saley's own
// action cards (D24: shown once, one click). Never a write the human already asked for.
// Numbers first (one count per kind), then the kind filter, then the list, 10 a page.
// The Ask card stays on Today only (R4 D131): the Inbox has no question box.
// Hooks: `#inbox-list .inbox-card[data-kind][data-id]`, `[data-act]` on each control,
// `[data-stat="<kind>"]`, `[data-filter="<kind>"]`. The kind lives in the hash (`#asks?type=card`).
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { FilterBar } from "@/components/saley/FilterBar";
import { Pager, PAGE_SIZE } from "@/components/saley/Pager";
import { Stat } from "@/components/saley/Stat";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { useTicks } from "@/hooks/use-press";
import { useRoute } from "@/hooks/use-route";
import { go } from "@/lib/route";
import { sumKnown } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { InboxCard } from "./InboxCard";
import { INBOX_KINDS, type InboxData, type InboxKind } from "./types";

const ALL = "all";

function listPath(type: string, offset: number): string {
  const kind = type === ALL ? "" : `&type=${type}`;
  return `/api/inbox?limit=${PAGE_SIZE}&offset=${offset}${kind}`;
}

/** One count per kind: `limit=1` asks only for the total. */
function useCount(kind: InboxKind) {
  return useApi<InboxData>(`/api/inbox?type=${kind}&limit=1`);
}

export function InboxPage({ view }: { view: ViewDef }) {
  const asked = useRoute().params.type ?? ALL;
  const type = INBOX_KINDS.some((k) => k.kind === asked) ? asked : ALL;
  const [offset, setOffset] = useOffset(type);
  const list = useApi<InboxData>(listPath(type, offset));
  const kept = useKeptPage(list.data, offset);
  const counts = {
    card: useCount("card"),
    confirm: useCount("confirm"),
    ask: useCount("ask"),
    reminder: useCount("reminder"),
  };
  const { ticks, now, setTick } = useTicks();
  const reload = () => {
    list.reload();
    Object.values(counts).forEach((c) => c.reload());
  };
  const n = (kind: InboxKind) => counts[kind].data?.total ?? null;
  const total = sumKnown(INBOX_KINDS.map((k) => n(k.kind)));
  const options = [
    { id: ALL, label: fmt(W.tabCount, { label: W.tabAll, n: total ?? "—" }) },
    ...INBOX_KINDS.map((k) => ({
      id: k.kind,
      label: fmt(W.tabCount, { label: k.name, n: n(k.kind) ?? "—" }),
    })),
  ];
  const error = list.error ?? Object.values(counts).find((c) => c.error)?.error ?? null;
  const page = kept?.page;

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatAsks} error={error} onRetry={reload} />}
      {!page && list.loading && <LoadingLine />}
      {page && (
        <div data-content className="flex flex-col gap-5" aria-busy={list.loading || undefined}>
          <div id="tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {INBOX_KINDS.map((k) => (
              <Stat key={k.kind} id={k.kind} label={k.name} value={n(k.kind)} />
            ))}
          </div>
          <FilterBar
            options={options}
            current={type}
            onPick={(id) => go("asks", id === ALL ? undefined : { type: id })}
            label={W.inboxKindLabel}
          />
          <section className="bg-card min-w-0 rounded-lg border" aria-label={W.navAsks}>
            <Pager
              total={page.total}
              offset={kept?.offset ?? offset}
              shown={page.items.length}
              onMove={setOffset}
              label={W.navAsks}
              className="pb-3 [&>nav]:px-4"
            >
              <div id="inbox-list" className="divide-y">
                {page.items.length === 0 && (
                  <div className="px-4 py-6">
                    <EmptyLine computed={page.last_computed_label} />
                  </div>
                )}
                {page.items.map((item) => (
                  <InboxCard
                    key={`${item.kind}:${item.id}`}
                    item={item}
                    tick={ticks[`${item.kind}:${item.id}`]}
                    now={now}
                    onTick={setTick}
                    onChanged={reload}
                  />
                ))}
              </div>
            </Pager>
          </section>
        </div>
      )}
    </ViewPage>
  );
}
