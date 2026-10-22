// One commitment (`#commit-table tbody tr[data-id]`). "Mark done" (`data-act="commitment-done"`)
// is for the owner only (D155). The page does not guess who the owner is: every open row has
// the Button, and the server decides. A press by anyone else gets 403 with the owner's name
// ("This is <name>'s commitment. Only they can mark it done."): the page shows it as a toast,
// nothing is written and the row stays as it was. For the owner, the SAME place turns into
// Undo (`data-act="undo"`) until `undo_until`, signed, as Today's tasks (brief).
import { CheckIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";
import { ApiError, postJson } from "@/api";
import { ActButton } from "@/components/saley/ActButton";
import { OneLine } from "@/components/saley/Links";
import { RowLine, type Line } from "@/components/saley/RowLine";
import { Tag } from "@/components/saley/Tag";
import { useToast } from "@/components/saley/Toaster";
import { TableCell, TableRow } from "@/components/ui/table";
import { usePress } from "@/hooks/use-press";
import { controlOf, tickFromAnswer, type Tick } from "@/lib/team-rules";
import { cn } from "@/lib/utils";
import { dueTag } from "@/views/today/task-rules";
import { fmt, W } from "@/words";
import { STATE_TAG, type Commitment, type CommitState, type DoneAnswer } from "./types";

const FORBIDDEN = 403;
const CONFLICT = 409;

export function CommitmentRow({
  item,
  tick,
  now,
  today,
  onTick,
  onChanged,
}: {
  item: Commitment;
  tick: Tick | null | undefined;
  now: number;
  today: string;
  onTick: (key: string, tick: Tick | null) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const { busy, press } = usePress();
  const [line, setLine] = useState<Line | null>(null);
  const [closed, setClosed] = useState(false);
  const key = String(item.id);
  const control = closed ? "closed" : controlOf(tick, now);
  const ticked = control !== "act";
  const state = (ticked ? "done" : item.state) as CommitState;
  const stateTag = STATE_TAG[state] ?? STATE_TAG.open;
  const due = dueTag(item.due, today);
  const endpoint = `/api/commitment/${item.id}/done`;

  const done = async () => {
    setLine(null);
    const err = await press(async () => {
      const answer = await postJson<DoneAnswer>(endpoint);
      const made = tickFromAnswer(answer);
      if (made) onTick(key, made);
      else setClosed(true); // already done: no Undo window here
    });
    if (!err) return;
    // D155: a non-owner press. The server's line names the owner; the row does not change.
    if (err.status === FORBIDDEN) toast(err.message, "error");
    else setLine({ text: err.message, bad: err.status !== CONFLICT });
  };

  const undo = async () => {
    if (!tick) return;
    setLine(null);
    const err = await press(async () => {
      try {
        await postJson(`/api/undo/${tick.writeId}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === CONFLICT) {
          onTick(key, { writeId: tick.writeId, until: 0 }); // the window ended on the server
        }
        throw e;
      }
      onTick(key, null);
      onChanged();
    });
    if (err) setLine({ text: err.message, bad: err.status !== CONFLICT });
  };

  return (
    <TableRow data-row data-id={item.id} data-state={state} className={cn(ticked && "is-done")}>
      <TableCell className="py-2.5">
        <div className="flex min-w-0 flex-col gap-1">
          <span className={cn("font-medium", ticked && "text-muted-foreground line-through")}>
            <OneLine text={item.text} />
          </span>
          <RowLine line={line} />
        </div>
      </TableCell>
      <TableCell className="py-2.5">
        <OneLine text={item.owner_name} />
      </TableCell>
      <TableCell className="py-2.5">
        <Tag tag={due.tag} tone={due.tone} className="nums">
          {item.due_label ?? W.cNoDue}
        </Tag>
      </TableCell>
      <TableCell className="text-muted-foreground py-2.5">
        <OneLine text={item.source} />
      </TableCell>
      <TableCell className="py-2.5">
        <div className="flex flex-col items-start gap-1">
          <Tag tag={state} tone={stateTag.tone}>
            {stateTag.name}
          </Tag>
          {item.in_pack && <span className="text-muted-foreground text-xs">{W.cInPack}</span>}
        </div>
      </TableCell>
      <TableCell className="tabular py-2.5 text-right">{item.reminders_sent}</TableCell>
      <TableCell className="py-2.5 text-right">
        {control === "act" && item.state === "open" && (
          <ActButton
            act="commitment-done"
            endpoint={endpoint}
            busy={busy}
            label={fmt(W.cDoneAria, { t: item.text })}
            onPress={() => void done()}
            className="min-w-24"
          >
            <CheckIcon aria-hidden />
            {W.cMarkDone}
          </ActButton>
        )}
        {control === "undo" && tick && (
          <ActButton
            act="undo"
            endpoint={`/api/undo/${tick.writeId}`}
            busy={busy}
            variant="secondary"
            label={fmt(W.undoAria, { t: item.text })}
            onPress={() => void undo()}
            className="min-w-24"
          >
            <Undo2Icon aria-hidden />
            {W.undoBtn}
          </ActButton>
        )}
        {control === "closed" && (
          <span className="text-muted-foreground inline-flex items-center gap-1 text-sm">
            <CheckIcon aria-hidden className="size-4" />
            {W.doneState}
          </span>
        )}
      </TableCell>
    </TableRow>
  );
}
