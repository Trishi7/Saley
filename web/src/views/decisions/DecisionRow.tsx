// One decision (`#dec-table tbody tr[data-id][data-kind]`, the legacy hook). Confirm
// (`data-act="confirm"`, approver) on a proposed strategy decision; Undo (`data-act="undo"`) on
// a column write, task tick, commitment Done or row add while its window is open. The legacy
// POSTs: /api/decision/{id}/confirm and /api/undo/{id}. After either, the list loads again.
import { CheckIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";
import { postJson } from "@/api";
import { ActButton } from "@/components/saley/ActButton";
import { OneLine } from "@/components/saley/Links";
import { RowLine, type Line } from "@/components/saley/RowLine";
import { Tag } from "@/components/saley/Tag";
import { TableCell, TableRow } from "@/components/ui/table";
import { usePress } from "@/hooks/use-press";
import { dayLabel } from "@/lib/format";
import { istTime } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { NO_UNDO_KINDS, STATE_TAGS, SURFACES, SYNC_TAGS, UNDO_TAGS, type Decision } from "./types";

const CONFLICT = 409;

function appliesTo(d: Decision): string | null {
  const what = d.what && d.value != null ? fmt(W.whatValue, { w: d.what, v: d.value }) : d.what;
  return [what, d.person].filter(Boolean).join(" · ") || null;
}

export function DecisionRow({ d, onChanged }: { d: Decision; onChanged: () => void }) {
  const { busy, press } = usePress();
  const [line, setLine] = useState<Line | null>(null);
  const state = STATE_TAGS[d.state];
  const undo = NO_UNDO_KINDS.has(d.kind) ? undefined : UNDO_TAGS[d.undo_state];
  const sync = d.sync_state ? SYNC_TAGS[d.sync_state] : undefined;
  const surface = SURFACES.find((s) => s.id === d.surface)?.name ?? d.surface;
  const what = [d.label, d.text].filter(Boolean).join(" · ") || null;

  const post = async (path: string) => {
    setLine(null);
    const err = await press(async () => {
      await postJson(path);
      onChanged();
    });
    if (err) setLine({ text: err.message, bad: err.status !== CONFLICT });
  };

  return (
    <TableRow data-row data-id={d.id} data-kind={d.kind}>
      <TableCell className="nums text-muted-foreground py-2.5 text-xs">{d.id}</TableCell>
      <TableCell className="nums py-2.5 text-xs">
        <span className="block">{fmt(W.dateTime, { d: dayLabel(d.at), t: istTime(d.at) })}</span>
        <span className="text-muted-foreground block">{surface ?? "—"}</span>
      </TableCell>
      <TableCell className="py-2.5">
        <OneLine text={d.user_name} />
      </TableCell>
      <TableCell className="py-2.5">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="font-medium">
            <OneLine text={what} />
          </span>
          <span className="text-muted-foreground text-xs">
            <OneLine text={appliesTo(d)} />
          </span>
          <RowLine line={line} />
        </div>
      </TableCell>
      <TableCell className="py-2.5">
        <div className="flex flex-wrap items-center gap-1">
          {state ? (
            <Tag tag={state.tag} tone={state.tone}>
              {state.name}
            </Tag>
          ) : (
            <span className="text-muted-foreground">{d.state}</span>
          )}
          {undo && (
            <Tag tag={undo.tag} tone={undo.tone}>
              {undo.name}
            </Tag>
          )}
          {sync && (
            <span data-sync={d.sync_state}>
              <Tag tag={sync.tag} tone={sync.tone}>
                {sync.name}
              </Tag>
            </span>
          )}
        </div>
        {d.sync_reason && <p className="text-muted-foreground mt-1 text-xs">{d.sync_reason}</p>}
        {d.confirmed_by_name && (
          <p className="text-muted-foreground mt-1 text-xs">
            {fmt(W.confirmedBy, { n: d.confirmed_by_name })}
          </p>
        )}
      </TableCell>
      <TableCell className="py-2.5 text-right">
        <div className="flex flex-wrap justify-end gap-2">
          {d.can_confirm && (
            <ActButton
              act="confirm"
              endpoint={`/api/decision/${d.id}/confirm`}
              busy={busy}
              variant="default"
              label={fmt(W.confirmAria, { t: what ?? String(d.id) })}
              onPress={() => void post(`/api/decision/${d.id}/confirm`)}
            >
              <CheckIcon aria-hidden />
              {W.confirmBtn}
            </ActButton>
          )}
          {d.can_undo && (
            <ActButton
              act="undo"
              endpoint={`/api/undo/${d.id}`}
              busy={busy}
              variant="secondary"
              label={fmt(W.undoAria, { t: what ?? String(d.id) })}
              onPress={() => void post(`/api/undo/${d.id}`)}
            >
              <Undo2Icon aria-hidden />
              {W.undoBtn}
            </ActButton>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}
