// Global search results (docs/08 Shell: "Global search opens a results page: people,
// companies, news, decisions, with links"). Same API and hooks as the legacy page:
// GET /api/search?q=, `#search-results`, groups `[data-group]`, hits `.hit` with the match in
// <mark>, and `.empty-state` for zero results. Each hit is a ClickCard. A person or a company
// opens its drawer in place (T14 drawers, R5 T15; `[data-open-hit]`); a goal, a deliverable or
// a decision opens the list it belongs to.
import { useDrawerPair } from "./DrawerPair";
import { useApi } from "@/hooks/use-api";
import { useMe } from "@/hooks/use-me";
import { hashFor } from "@/lib/route";
import { fmt, W } from "@/words";
import { ClickCard } from "./ClickCard";
import { ErrorLine, LoadingLine } from "./states";
import { Tag } from "./Tag";
import { stageTone } from "./tones";
import { ViewPage } from "./ViewPage";
import type { ViewDef } from "./views";

type Hit = {
  type: string;
  id: number | string | null;
  name: string;
  context: string;
  pill: string | null;
  owner: string | null;
  match: [number, number] | null;
};
type SearchOut = {
  q: string;
  total: number;
  groups: Record<string, { items: Hit[]; total: number }>;
};

const GROUPS: [string, string][] = [
  ["people", W.gPeople],
  ["companies", W.gCompanies],
  ["goals", W.gGoals],
  ["deliverables", W.gDeliverables],
  ["decisions", W.gDecisions],
];

const OPENS: Record<string, string> = {
  person: "people",
  company: "accounts",
  goal: "goals",
  deliverable: "blockers",
  decision: "decisions",
};

/** Bold the matched part with text nodes only: sheet text is data, never markup. */
function Marked({ name, match }: { name: string; match: [number, number] | null }) {
  if (!match) return <>{name}</>;
  const [at, n] = match;
  return (
    <>
      {name.slice(0, at)}
      <mark className="bg-warn-soft text-foreground rounded-sm px-0.5">{name.slice(at, at + n)}</mark>
      {name.slice(at + n)}
    </>
  );
}

/** The legend key of a hit's pill: a goal, a known stage colour (D201), or "stage". */
function pillTag(type: string, pill: string): string {
  if (type === "goal") return "goal";
  const tone = stageTone(pill);
  return tone.startsWith("stage-") ? tone : "stage";
}

/** A numeric id of a person or company hit, or null (then the hit opens its list). */
function drawerId(hit: Hit): number | null {
  if (hit.type !== "person" && hit.type !== "company") return null;
  const id = typeof hit.id === "number" ? hit.id : Number(hit.id);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function HitCard({
  hit,
  ownerName,
  onOpen,
}: {
  hit: Hit;
  ownerName: (o: string) => string;
  onOpen: (hit: Hit, id: number, from: HTMLElement) => void;
}) {
  const view = OPENS[hit.type] ?? "today";
  const params: Record<string, string> = hit.type === "person" || hit.type === "company"
    ? { filter: "all", q: hit.name }
    : {};
  const context = hit.owner ? `${hit.context} · ${ownerName(hit.owner)}` : hit.context;
  const id = drawerId(hit);
  const body = (
    <>
      <span className="flex flex-wrap items-center gap-2">
        <b className="font-medium">
          <Marked name={hit.name} match={hit.match} />
        </b>
        {hit.pill && (
          <Tag tag={pillTag(hit.type, hit.pill)} tone={stageTone(hit.pill)}>
            {hit.pill}
          </Tag>
        )}
      </span>
      <span className="ctx text-muted-foreground mt-1 block text-sm">{context}</span>
    </>
  );
  if (id !== null) {
    return (
      <ClickCard
        className="hit p-3"
        data-row
        data-open-hit={hit.type}
        aria-label={fmt(hit.type === "person" ? W.openPerson : W.openCompany, { name: hit.name })}
        onClick={(e) => onOpen(hit, id, e.currentTarget)}
      >
        {body}
      </ClickCard>
    );
  }
  return (
    <ClickCard href={hashFor(view, params)} className="hit p-3" data-row>
      {body}
    </ClickCard>
  );
}

export function SearchView({ view, q }: { view: ViewDef; q: string }) {
  const { me } = useMe();
  const query = q.trim();
  const { data, error, loading, reload } = useApi<SearchOut>(
    query ? `/api/search?q=${encodeURIComponent(query)}` : null,
  );
  const { openPerson, openCompany, drawers } = useDrawerPair();
  const open = (hit: Hit, id: number, from: HTMLElement) => {
    if (hit.type === "person") openPerson({ contactId: id, name: hit.name, company: null }, from);
    else openCompany({ id, name: hit.name }, from);
  };
  const ownerName = (o: string) =>
    me?.people.find((p) => p.short.toLowerCase() === o.toLowerCase())?.name ?? o;
  return (
    <ViewPage
      view={view}
      title={query ? fmt(W.searchTitleQ, { q: query }) : W.navSearch}
      sub={query ? "" : W.searchHint}
    >
      <div id="search-results" data-content className="flex flex-col gap-4">
        {loading && <LoadingLine />}
        {error && <ErrorLine what={W.whatSearch} error={error} onRetry={reload} />}
        {data && data.total === 0 && (
          <div className="empty-state bg-card rounded-lg border p-6">
            <h2 className="font-medium">{fmt(W.zeroResults, { q: query })}</h2>
            <p className="text-muted-foreground mt-1 text-sm">{W.zeroHint}</p>
          </div>
        )}
        {data && data.total > 0 && (
          <div className="grid gap-4 lg:grid-cols-2">
            {GROUPS.filter(([key]) => (data.groups[key]?.total ?? 0) > 0).map(([key, label]) => {
              const group = data.groups[key];
              return (
                <section key={key} data-group={key} className="flex flex-col gap-2">
                  <h2 className="text-sm font-medium">
                    {label} <span className="text-muted-foreground tabular">{group.total}</span>
                  </h2>
                  {group.items.map((hit) => (
                    <HitCard
                      key={`${hit.type}-${hit.id ?? hit.name}`}
                      hit={hit}
                      ownerName={ownerName}
                      onOpen={open}
                    />
                  ))}
                  {group.total > group.items.length && (
                    <p className="text-muted-foreground text-sm">
                      {fmt(W.gMore, { n: group.total - group.items.length })}
                    </p>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </div>
      {drawers}
    </ViewPage>
  );
}
