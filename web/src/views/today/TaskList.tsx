// Today, part 2: "Your tasks today" (docs/08 view 1; plan §3.6).
// - 10 per page in the server's order (D152, D180); paging is the server's (`tasks_offset`).
// - Whose list: the signed-in person's own list, always (D207: no person picker, no View as).
//   The signed-in person is the signer of every press (D50).
// - Ticks: a Done made on this page keeps its Undo until `undo_until`; a reload gets the same
//   from the server (`undo_id`, `undo_until`). When the window ends the row shows "Done" and
//   leaves on the next refresh. Nothing counts down on the page (brief: no timer text).
import { useEffect, useState } from "react";
import { Pager } from "@/components/saley/Pager";
import { W } from "@/words";
import { istToday, tickOf, type Tick } from "./task-rules";
import { TaskRow } from "./TaskRow";
import type { MyTasks, Task } from "./types";

export function TaskList({
  block,
  onMove,
  onChanged,
  onAddEmail,
}: {
  block: MyTasks;
  onMove: (offset: number) => void;
  /** Fetch the page again (after an Undo, so the row gets its Done back). */
  onChanged: () => void;
  onAddEmail: (task: Task) => void;
}) {
  // key -> this page's tick; null = undone here (ignore the server's tick until the refetch).
  const [ticks, setTicks] = useState<Record<string, Tick | null>>({});
  const [now, setNow] = useState(() => Date.now());

  // Wake once when the next Undo window ends, so that row turns into "Done".
  const nextEnd = Math.min(
    ...block.items.map((t) => tickOf(t, ticks[t.key])?.until ?? Infinity).filter((u) => u > now),
  );
  useEffect(() => {
    if (!Number.isFinite(nextEnd)) return undefined;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, nextEnd - Date.now()));
    return () => window.clearTimeout(timer);
  }, [nextEnd]);

  const setTick = (key: string, tick: Tick | null) => {
    setNow(Date.now());
    setTicks((all) => ({ ...all, [key]: tick }));
  };

  const today = istToday(now);
  return (
    <section aria-labelledby="tasks-title" className="bg-card flex flex-col rounded-lg border">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex min-w-0 flex-col">
          <h2 id="tasks-title" className="text-base font-semibold">
            {W.tasksTitle}
          </h2>
          <p className="text-muted-foreground text-xs">{W.tasksHint}</p>
        </div>
      </header>
      <Pager
        total={block.total}
        offset={block.offset}
        shown={block.items.length}
        onMove={onMove}
        label={W.tasksTitle}
        className="pb-3 [&>nav]:px-4"
      >
        <ul id="tasks" className="tasks divide-y">
          {block.items.length === 0 && (
            <li className="empty text-muted-foreground px-4 py-6 text-sm">{block.empty}</li>
          )}
          {block.items.map((task) => (
            <TaskRow
              key={task.key}
              task={task}
              tick={tickOf(task, ticks[task.key])}
              now={now}
              today={today}
              onTick={setTick}
              onRefetch={onChanged}
              onAddEmail={onAddEmail}
            />
          ))}
        </ul>
      </Pager>
    </section>
  );
}
