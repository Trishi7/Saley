// One row of "Your tasks today" (`#tasks li.task`, Appendix B).
// Done is one POST of the task's own action (the server picks the value, D153). On success the
// SAME button becomes Undo (`[data-act="undo"]`) until `undo_until`: no timer text, no bottom
// bar, no toast (brief, plan §3.6). One Undo undoes every cell of that Done (D168: the server
// undoes the whole group of the write id). A refusal (e.g. 409 "This column has no done value
// yet.") shows the server's line in the row. "Add email" has no Done: it opens the drawer (D154).
import { ArrowRightIcon, CheckIcon, MailPlusIcon, Undo2Icon } from "lucide-react";
import { useRef, useState } from "react";
import { ApiError, postJson } from "@/api";
import { ComingSoon } from "@/components/saley/ComingSoon";
import { Tag } from "@/components/saley/Tag";
import { Button } from "@/components/ui/button";
import { hrefFor } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { dueTag, kindTag, rowControl, tickFrom, type Tick } from "./task-rules";
import type { ActAnswer, Task } from "./types";

export function TaskRow({
  task,
  tick,
  now,
  today,
  onTick,
  onRefetch,
  onAddEmail,
}: {
  task: Task;
  tick: Tick | null;
  now: number;
  today: string;
  onTick: (key: string, tick: Tick | null) => void;
  /** Fetch the list again (after an Undo, or a Done with no Undo window). */
  onRefetch: () => void;
  onAddEmail: (task: Task) => void;
}) {
  const [busy, setBusy] = useState(false);
  // A refusal is red; a calm server line (already done, window ended) is neutral.
  const [line, setLine] = useState<{ text: string; bad: boolean } | null>(null);
  const busyRef = useRef(false); // one press is one POST (gate step 9)
  const control = rowControl(task, tick, now);
  const kind = kindTag(task.kind);
  const due = dueTag(task.due, today);

  async function press(run: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setLine(null);
    try {
      await run();
    } catch (err) {
      setLine({ text: err instanceof ApiError ? err.message : W.errGeneric, bad: true });
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const done = () =>
    press(async () => {
      if (!task.action) return;
      const answer = await postJson<ActAnswer>(task.action.endpoint, task.action.body ?? undefined);
      const made = tickFrom(answer);
      if (made) {
        onTick(task.key, made);
        return;
      }
      // Done, but no Undo window (already done, or no undo time): close the row here, say the
      // server's line calmly, and fetch the list again. Done is never pressable twice.
      onTick(task.key, { writeId: answer.write_id, until: 0 });
      setLine({ text: answer.label, bad: false });
      onRefetch();
    });

  const undo = () =>
    press(async () => {
      if (!tick) return;
      try {
        await postJson(`/api/undo/${tick.writeId}`);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        // The window ended on the server (or the clocks differ): the tick stays, no Undo.
        onTick(task.key, { writeId: tick.writeId, until: 0 });
        setLine({ text: err.message, bad: false });
        onRefetch();
        return;
      }
      onTick(task.key, null);
      onRefetch();
    });

  const ticked = control === "undo" || control === "closed";
  return (
    <li
      data-row
      data-key={task.key}
      data-kind={task.kind}
      className={cn(
        "task flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between",
        ticked && "is-done",
      )}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag tag={kind.tag} tone={kind.tone}>
            {kind.name}
          </Tag>
          <Tag tag={due.tag} tone={due.tone} className="t-due tabular">
            {task.due_label ?? W.noDate}
          </Tag>
        </div>
        <span
          className={cn(
            "t-title text-sm font-medium text-pretty",
            ticked && "text-muted-foreground line-through decoration-1",
          )}
        >
          {task.title}
        </span>
        {task.why && !ticked && (
          <span className="t-why text-muted-foreground text-xs text-pretty">{task.why}</span>
        )}
        {line && (
          <span
            role="status"
            data-row-error={line.bad || undefined}
            data-row-line
            className={cn("text-xs text-pretty", line.bad ? "text-bad" : "text-muted-foreground")}
          >
            {line.text}
          </span>
        )}
      </div>
      <div className="t-act flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
        {(control === "done" || control === "undo") && (
          <Button
            type="button"
            size="sm"
            variant={control === "undo" ? "secondary" : "outline"}
            data-act={control}
            data-endpoint={control === "undo" ? `/api/undo/${tick?.writeId}` : task.action?.endpoint}
            aria-label={fmt(control === "undo" ? W.undoAria : W.doneAria, { t: task.title })}
            aria-busy={busy || undefined}
            // Not `disabled`: a disabled button drops focus to <body>. busyRef blocks a second
            // press; the same button keeps focus through Done -> Undo -> Done.
            aria-disabled={busy || undefined}
            onClick={() => void (control === "undo" ? undo() : done())}
            className="h-10 min-w-24 aria-disabled:cursor-wait aria-disabled:opacity-60 sm:h-8"
          >
            {control === "undo" ? <Undo2Icon aria-hidden /> : <CheckIcon aria-hidden />}
            {control === "undo" ? W.undoBtn : W.doneBtn}
          </Button>
        )}
        {control === "closed" && (
          <span className="text-muted-foreground flex items-center gap-1 text-sm">
            <CheckIcon aria-hidden className="size-4" />
            {W.doneState}
          </span>
        )}
        {control === "add_email" && (
          <>
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-add-email
              aria-label={fmt(W.addEmailAria, { t: task.title })}
              onClick={() => onAddEmail(task)}
              className="h-10 sm:h-8"
            >
              <MailPlusIcon aria-hidden />
              {W.addEmail}
            </Button>
            {task.soon === "email_write" && (
              <span data-feature="email_write">
                <ComingSoon feature="email_write" />
              </span>
            )}
          </>
        )}
        {control === "link" && task.link && (
          <Button asChild size="sm" variant="outline" className="h-10 sm:h-8">
            <a href={hrefFor(task.link)}>
              {W.openInbox}
              <ArrowRightIcon aria-hidden />
            </a>
          </Button>
        )}
      </div>
    </li>
  );
}
