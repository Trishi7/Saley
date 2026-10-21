// The Today page of the dashboard v2 (R5 T13; docs/08 view 1). Page order (brief, D186):
// numbers -> your tasks -> needs a look -> Saley suggests -> the Ask card at the foot.
// No page search box: the global search in the top bar stays (brief Today 1).
// One GET /api/today feeds the page; a new task page fetches it again, and the last answer
// stays on screen meanwhile, so paging never blanks the page. The list is always the
// signed-in person's own (D207: no person picker; the server reads the session, never ?user=).
import { useCallback, useState } from "react";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { PersonDrawer, type DrawerPerson, type SavedEmail } from "@/components/saley/PersonDrawer";
import { useToast } from "@/components/saley/Toaster";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { fmt, W } from "@/words";
import { AskCard } from "./AskCard";
import { NeedsLook } from "./NeedsLook";
import { Numbers } from "./Numbers";
import { Suggests } from "./Suggests";
import { TaskList } from "./TaskList";
import type { Task, TodayData } from "./types";

export function TodayPage({ view }: { view: ViewDef }) {
  const toast = useToast();
  const [offset, setOffset] = useState(0);
  // No `?user=`: the server reads the session (D207). Only the task page moves.
  const path = offset ? `/api/today?tasks_offset=${offset}` : "/api/today";
  const { data, error, loading, reload } = useApi<TodayData>(path);

  // Keep the last answer while the next one loads (React: state from the previous render).
  const [kept, setKept] = useState<TodayData | undefined>(data);
  if (data !== undefined && data !== kept) setKept(data);
  const shown = data ?? kept;

  const [drawer, setDrawer] = useState<DrawerPerson | null>(null);
  const openEmail = useCallback((task: Task) => {
    if (task.contact_id === null) return;
    setDrawer({ contactId: task.contact_id, name: task.name, company: task.company, focusEmail: true });
  }, []);
  const onSaved = useCallback(
    (saved: SavedEmail) => {
      setDrawer(null);
      toast(saved.label);
      reload(); // the server hides the task once the email is saved (D154, D169)
    },
    [reload, toast],
  );
  const onMove = (next: number) => setOffset(next);

  const sub = shown ? fmt(W.todaySub, { hello: shown.greeting, date: shown.date_label }) : undefined;
  return (
    <ViewPage view={view} sub={sub}>
      {error && <ErrorLine what={W.whatToday} error={error} onRetry={reload} />}
      {!shown && loading && <LoadingLine />}
      {!shown && !loading && !error && <EmptyLine />}
      {shown && (
        <div data-content className="flex flex-col gap-8" aria-busy={loading || undefined}>
          <Numbers data={shown} />
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:items-start">
            <TaskList
              block={shown.my_tasks}
              onMove={onMove}
              onChanged={reload}
              onAddEmail={openEmail}
            />
            <div className="flex min-w-0 flex-col gap-8">
              <NeedsLook block={shown.needs_look} />
              <Suggests block={shown.saley_suggests} />
            </div>
          </div>
          <AskCard />
        </div>
      )}
      <PersonDrawer person={drawer} onClose={() => setDrawer(null)} onSaved={onSaved} />
    </ViewPage>
  );
}
