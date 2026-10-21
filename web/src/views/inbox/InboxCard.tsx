// One Inbox item (`#inbox-list .inbox-card`, the legacy hook kept for the cut-over).
// The same POSTs and `data-act` names as the legacy page (static/team.js):
// - `primary`: the card's own action (POST /api/act) or a decision confirm
//   (POST /api/decision/{id}/confirm). A card write turns into Undo in place until
//   `undo_until` (as Today's tasks); a confirm has no Undo and shows "Confirmed".
// - `edit-save`: only on a card with ONE draft cell (D129, D186). The first saver wins; the
//   other gets the server's "Already done by X at HH:MM" line (D137), shown calmly.
// - `dismiss` ("Skip"): the card leaves the list.
// - `undo`: POST /api/undo/{write_id}, the whole group of that write (D168).
import { CheckIcon, PencilIcon, SkipForwardIcon, Undo2Icon } from "lucide-react";
import { useState } from "react";
import { ApiError, postJson } from "@/api";
import { ActButton } from "@/components/saley/ActButton";
import { RowLine, type Line } from "@/components/saley/RowLine";
import { Tag } from "@/components/saley/Tag";
import { useToast } from "@/components/saley/Toaster";
import { Button } from "@/components/ui/button";
import { usePress } from "@/hooks/use-press";
import { dayLabel } from "@/lib/format";
import { controlOf, draftOf, tickFromAnswer, type Tick } from "@/lib/team-rules";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { KIND_TAG, type InboxItem, type WriteAnswer } from "./types";

const EDIT_MAX = 200; // schemas.ProposalEditIn

function Editor({
  id,
  text,
  busy,
  onSave,
  onCancel,
}: {
  id: number;
  text: string;
  busy: boolean;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(text);
  const field = `edit-${id}`;
  return (
    <div className="edit-row flex flex-col gap-2">
      <label htmlFor={field} className="text-muted-foreground text-xs">
        {W.editHint}
      </label>
      <textarea
        id={field}
        aria-label={W.editLabel}
        maxLength={EDIT_MAX}
        rows={2}
        value={value}
        autoFocus
        onChange={(e) => setValue(e.target.value)}
        className={cn(
          "border-input bg-background w-full rounded-md border px-3 py-2 text-sm",
          "focus-visible:border-ring focus-visible:ring-ring/50 outline-none focus-visible:ring-[3px]",
        )}
      />
      <div className="flex flex-wrap gap-2">
        <ActButton
          act="edit-save"
          endpoint={`/api/proposal/${id}/edit`}
          busy={busy}
          variant="default"
          onPress={() => onSave(value.trim())}
        >
          <CheckIcon aria-hidden />
          {W.editSave}
        </ActButton>
        <Button type="button" size="sm" variant="ghost" className="h-10 sm:h-8" onClick={onCancel}>
          {W.editCancel}
        </Button>
      </div>
    </div>
  );
}

export function InboxCard({
  item,
  tick,
  now,
  onTick,
  onChanged,
}: {
  item: InboxItem;
  tick: Tick | null | undefined;
  now: number;
  onTick: (key: string, tick: Tick | null) => void;
  /** Fetch the list again (after Skip or Undo). */
  onChanged: () => void;
}) {
  const toast = useToast();
  const { busy, press } = usePress();
  const [line, setLine] = useState<Line | null>(null);
  const [editing, setEditing] = useState(false);
  const [closed, setClosed] = useState<string | null>(null); // "Saved" or "Confirmed"
  const key = `${item.kind}:${item.id}`;
  const draft = draftOf(item);
  const control = closed ? "closed" : controlOf(tick, now);
  const kind = KIND_TAG[item.kind];

  async function run(job: () => Promise<void>) {
    setLine(null);
    const err = await press(job);
    // 409 is the server's calm line (already done, window closed); anything else is red.
    if (err) setLine({ text: err.message, bad: err.status !== 409 });
  }

  const written = (answer: WriteAnswer, state: string) => {
    const made = tickFromAnswer(answer);
    if (made) {
      onTick(key, made);
      setEditing(false);
      return;
    }
    setClosed(state);
    if (answer.label) setLine({ text: answer.label, bad: false });
  };

  const doIt = () =>
    run(async () => {
      if (!item.action) return;
      const answer = await postJson<WriteAnswer>(item.action.endpoint, item.action.body ?? undefined);
      written(answer, item.kind === "confirm" ? W.confirmedState : W.savedState);
    });

  const save = (text: string) => {
    if (!draft) return;
    if (!text) {
      setLine({ text: W.editEmpty, bad: true });
      return;
    }
    void run(async () => {
      const answer = await postJson<WriteAnswer>(`/api/proposal/${draft.id}/edit`, { text });
      written(answer, W.savedState);
    });
  };

  const skip = () =>
    run(async () => {
      if (!item.dismiss) return;
      await postJson(item.dismiss);
      toast(W.skippedLine);
      onChanged();
    });

  const undo = () =>
    run(async () => {
      if (!tick) return;
      try {
        await postJson(`/api/undo/${tick.writeId}`);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        onTick(key, { writeId: tick.writeId, until: 0 }); // the window ended on the server
        throw err;
      }
      onTick(key, null);
      onChanged();
    });

  const title = item.person ?? item.title;
  return (
    <article
      data-row
      data-kind={item.kind}
      data-id={item.id}
      className={cn(
        "inbox-card flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-start sm:justify-between",
        control !== "act" && "is-done",
      )}
    >
      <div className="ic-main flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag tag={item.kind} tone={kind.tone}>
            {kind.name}
          </Tag>
          <span className="text-muted-foreground tabular text-xs">{dayLabel(item.at)}</span>
        </div>
        <span className="text-sm font-medium text-pretty">{item.title}</span>
        {item.why && <span className="text-muted-foreground text-xs text-pretty">{item.why}</span>}
        <span className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
          {item.for_user && <span>{fmt(W.forUser, { u: item.for_user })}</span>}
          {item.evidence.row && <span>{fmt(W.sheetRow, { r: item.evidence.row })}</span>}
        </span>
        {editing && draft && control === "act" && (
          <Editor
            id={draft.id}
            text={draft.text}
            busy={busy}
            onSave={save}
            onCancel={() => setEditing(false)}
          />
        )}
        <RowLine line={line} />
      </div>
      <div className="ic-act flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
        {control === "undo" && tick && (
          <ActButton
            act="undo"
            endpoint={`/api/undo/${tick.writeId}`}
            busy={busy}
            variant="secondary"
            label={fmt(W.undoAria, { t: title })}
            onPress={() => void undo()}
            className="min-w-24"
          >
            <Undo2Icon aria-hidden />
            {W.undoBtn}
          </ActButton>
        )}
        {control === "closed" && (
          <span className="text-muted-foreground flex items-center gap-1 text-sm">
            <CheckIcon aria-hidden className="size-4" />
            {closed ?? W.doneState}
          </span>
        )}
        {control === "act" && item.action && (
          <ActButton
            act="primary"
            endpoint={item.action.endpoint}
            busy={busy}
            variant="default"
            label={fmt(item.kind === "confirm" ? W.confirmAria : W.doAria, { t: title })}
            onPress={() => void doIt()}
            className="min-w-24"
          >
            <CheckIcon aria-hidden />
            {item.button ?? (item.kind === "confirm" ? W.confirmBtn : W.doIt)}
          </ActButton>
        )}
        {control === "act" && draft && !editing && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={fmt(W.editAria, { t: title })}
            onClick={() => setEditing(true)}
            className="h-10 sm:h-8"
          >
            <PencilIcon aria-hidden />
            {W.editBtn}
          </Button>
        )}
        {control === "act" && item.dismiss && (
          <ActButton
            act="dismiss"
            endpoint={item.dismiss}
            busy={busy}
            variant="ghost"
            label={fmt(W.skipAria, { t: title })}
            onPress={() => void skip()}
          >
            <SkipForwardIcon aria-hidden />
            {W.skipBtn}
          </ActButton>
        )}
      </div>
    </article>
  );
}
