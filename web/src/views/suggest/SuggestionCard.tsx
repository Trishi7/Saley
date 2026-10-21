// One suggestion (R5 T15). First its one line from the API ("Add Priya Rao (Sarvam) to pipeline
// — hiring for evals"; built and value-checked on the server, R5 T5), then the score parts, the
// evidence and the acts: open the person or the company (the T14 drawers) and, for a new
// company, Find people and the search hits with "Add to pipeline". Find people is off (D164):
// its place shows `[data-soon="find_people"]` and the button is inert. When it is on, it is the
// legacy POST /api/act {action: "find_people", target_ids: [account]} from `[data-act="find"]`.
// Hooks: `[data-row][data-suggestion]` with `data-kind`, `[data-line]`, `[data-open-subject]`.
import { ArrowRightIcon, SearchIcon } from "lucide-react";
import { useRef, useState } from "react";
import { ApiError, postJson } from "@/api";
import { FeatureGate } from "@/components/saley/ComingSoon";
import { Button } from "@/components/ui/button";
import type { ActAnswer } from "@/views/today/types";
import { fmt, W } from "@/words";
import { HitRow } from "./HitRow";
import type { Suggestion } from "./types";

const ACT_PATH = "/api/act";
const PARTS_SHOWN = 4;

function evidenceLine(evidence: Record<string, unknown>): string {
  const bits: string[] = [];
  if (typeof evidence.row === "string" && evidence.row) bits.push(fmt(W.sheetRow, { r: evidence.row }));
  if (typeof evidence.industry === "string" && evidence.industry) bits.push(evidence.industry);
  if (typeof evidence.geo === "string" && evidence.geo) bits.push(evidence.geo);
  return bits.join(" · ");
}

function FindPeople({ item }: { item: Suggestion }) {
  const [state, setState] = useState(item.search?.message ?? "");
  const busy = useRef(false);
  const find = async () => {
    if (busy.current || item.account_id === null) return;
    busy.current = true;
    try {
      const answer = await postJson<ActAnswer>(ACT_PATH, {
        action: "find_people",
        target_ids: [item.account_id],
      });
      setState(answer.label);
    } catch (err) {
      setState(err instanceof ApiError ? err.message : W.errGeneric);
    } finally {
      busy.current = false;
    }
  };
  return (
    <FeatureGate feature="find_people">
      <span className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-act="find"
          data-endpoint={ACT_PATH}
          aria-label={fmt(W.findAria, { c: item.subject })}
          disabled={!item.can_find_people}
          onClick={() => void find()}
        >
          <SearchIcon aria-hidden />
          {W.featFindPeople}
        </Button>
        {state && <span className="text-muted-foreground text-xs" role="status">{state}</span>}
      </span>
    </FeatureGate>
  );
}

export function SuggestionCard({
  item,
  onOpen,
}: {
  item: Suggestion;
  onOpen: (item: Suggestion, from: HTMLElement) => void;
}) {
  const newco = item.kind === "newco";
  const sub = [newco ? null : item.company, item.title].filter(Boolean).join(" · ");
  const evidence = evidenceLine(item.evidence);
  const canOpen = newco ? item.account_id !== null : item.contact_id !== null;
  return (
    <li data-row data-suggestion={item.id} data-kind={item.kind} className="flex flex-col gap-3 px-4 py-4">
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <p data-line className="text-sm leading-snug font-medium text-pretty">
            {item.line ?? item.subject}
          </p>
          {sub && <p className="text-muted-foreground text-xs text-pretty">{sub}</p>}
        </div>
        <span className="tabular bg-muted shrink-0 rounded-full px-2 py-0.5 text-xs">
          {fmt(W.sgScore, { n: item.score })}
        </span>
      </div>
      {item.parts.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label={W.sgParts}>
          {item.parts.slice(0, PARTS_SHOWN).map((p) => (
            <li key={p.code} className="bg-secondary text-secondary-foreground rounded-md px-2 py-0.5 text-xs">
              {fmt(W.sgPart, { label: p.label, n: p.points })}
            </li>
          ))}
        </ul>
      )}
      {(item.via || evidence) && (
        <p className="text-muted-foreground text-xs text-pretty">
          {[item.via, evidence].filter(Boolean).join(" · ")}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {canOpen && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-open-subject={newco ? "company" : "person"}
            onClick={(e) => onOpen(item, e.currentTarget)}
          >
            {newco ? W.openCompanyBtn : W.openPersonBtn}
            <ArrowRightIcon aria-hidden />
          </Button>
        )}
        {newco && <FindPeople item={item} />}
      </div>
      {newco && (
        <div className="flex flex-col gap-1 border-t pt-2">
          <p className="text-xs font-medium">{W.hitsTitle}</p>
          {item.hits.length === 0 ? (
            <p className="text-muted-foreground text-xs">{W.noHits}</p>
          ) : (
            <ul className="divide-y">
              {item.hits.map((hit) => (
                <HitRow key={hit.id} hit={hit} accountId={item.account_id} />
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
