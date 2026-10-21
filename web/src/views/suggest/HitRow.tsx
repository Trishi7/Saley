// One search hit of a "new company" suggestion, with "Add to pipeline" (R5 T15, legacy R3 D76).
// The Add form shows the sheet values and the hit; the person may edit only the name and the
// designation. Add is ONE POST /api/act {action: "row_add", target_ids: [account], payload:
// {fields}} — the legacy body — from a `[data-act="add"]` button. On success the same button
// becomes Undo (`[data-act="undo"]`, POST /api/undo/<write id>) until the undo time, as the
// Today Done (T13): no timer text, no bottom bar. The hit link is an external Button, https
// only, rel noopener noreferrer; nothing on the page fetches it.
import { CheckIcon, PlusIcon, Undo2Icon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, postJson } from "@/api";
import { ExternalButton } from "@/components/saley/Links";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { tickFrom, type Tick } from "@/views/today/task-rules";
import type { ActAnswer } from "@/views/today/types";
import { fmt, W } from "@/words";
import type { SearchHit } from "./types";

const ACT_PATH = "/api/act";
const FIELD_MAX = 200; // the legacy inputs' maxlength

type Status = { kind: "idle" } | { kind: "undo"; tick: Tick } | { kind: "added" };

export function safeHref(href: string | null | undefined): string | null {
  return href && href.startsWith("https://") ? href : null;
}

function valueOf(hit: SearchHit, role: string): string {
  return hit.form.fields.find((f) => f.role === role)?.value ?? "";
}

export function HitRow({ hit, accountId }: { hit: SearchHit; accountId: number | null }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(() => valueOf(hit, "name"));
  const [role, setRole] = useState(() => valueOf(hit, "designation"));
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [line, setLine] = useState<{ text: string; bad: boolean } | null>(null);
  const busy = useRef(false);
  const formId = useId();
  const href = safeHref(hit.href);
  const link = valueOf(hit, "li_url");
  const industry = valueOf(hit, "industry");

  // The Undo window ends: the button becomes the calm "Added" state (no timer on screen).
  useEffect(() => {
    if (status.kind !== "undo") return undefined;
    const timer = window.setTimeout(
      () => setStatus({ kind: "added" }),
      Math.max(0, status.tick.until - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [status]);

  async function press(run: () => Promise<void>) {
    if (busy.current) return; // one press is one POST (gate step 9)
    busy.current = true;
    setLine(null);
    try {
      await run();
    } catch (err) {
      setLine({ text: err instanceof ApiError ? err.message : W.errGeneric, bad: true });
    } finally {
      busy.current = false;
    }
  }

  const add = () =>
    press(async () => {
      const typed = name.trim();
      if (!typed || accountId === null) {
        setLine({ text: W.addNeedName, bad: true });
        return;
      }
      const fields = [{ role: "name", value: typed }];
      if (role.trim()) fields.push({ role: "designation", value: role.trim() });
      hit.form.fields
        .filter((f) => f.role !== "name" && f.role !== "designation")
        .forEach((f) => fields.push(f));
      const body = { action: "row_add", target_ids: [accountId], payload: { fields } };
      const answer = await postJson<ActAnswer>(ACT_PATH, body);
      const tick = tickFrom(answer);
      setStatus(tick ? { kind: "undo", tick } : { kind: "added" });
      if (!tick && answer.label) setLine({ text: answer.label, bad: false });
    });

  const undo = () =>
    press(async () => {
      if (status.kind !== "undo") return;
      try {
        await postJson(`/api/undo/${status.tick.writeId}`);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 409) throw err;
        // The window ended on the server: the row stays added, said calmly.
        setStatus({ kind: "added" });
        setLine({ text: err.message, bad: false });
        return;
      }
      setStatus({ kind: "idle" });
      setLine({ text: W.undoneLine, bad: false });
    });

  return (
    <li data-hit={hit.id} className={cn("flex flex-col gap-2 py-2.5", status.kind !== "idle" && "is-done")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-sm text-pretty">{hit.title}</span>
        <span className="text-muted-foreground text-xs">{fmt(W.hitFrom, { e: hit.engine })}</span>
        {href && (
          <ExternalButton href={href} label={fmt(W.openHitAria, { t: hit.title })}>
            {W.openHit}
          </ExternalButton>
        )}
        {status.kind === "idle" && (
          <Button
            type="button"
            size="sm"
            variant={open ? "secondary" : "outline"}
            aria-expanded={open}
            aria-controls={formId}
            data-open="add"
            onClick={() => setOpen(!open)}
          >
            <PlusIcon aria-hidden />
            {W.addOpen}
          </Button>
        )}
      </div>
      {open && (
        <div id={formId} data-form="add" className="bg-muted/50 flex flex-col gap-3 rounded-md border p-3">
          <dl className="text-muted-foreground grid gap-x-3 gap-y-1 text-xs sm:grid-cols-[auto_1fr]">
            <dt>{W.addCompany}</dt>
            <dd className="text-foreground min-w-0 truncate">{hit.form.company}</dd>
            {link && <dt>{W.addLink}</dt>}
            {link && <dd className="text-foreground min-w-0 truncate">{link}</dd>}
            {industry && <dt>{W.addIndustry}</dt>}
            {industry && <dd className="text-foreground min-w-0 truncate">{industry}</dd>}
          </dl>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs font-medium">
              {W.addName}
              <input
                value={name}
                maxLength={FIELD_MAX}
                onChange={(e) => setName(e.target.value)}
                disabled={status.kind !== "idle"}
                className="border-input bg-background h-10 rounded-md border px-3 text-sm font-normal outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium">
              {W.addRole}
              <input
                value={role}
                maxLength={FIELD_MAX}
                onChange={(e) => setRole(e.target.value)}
                disabled={status.kind !== "idle"}
                className="border-input bg-background h-10 rounded-md border px-3 text-sm font-normal outline-none focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </label>
          </div>
          <p className="text-muted-foreground text-xs">{W.addNote}</p>
          <div className="flex flex-wrap items-center gap-2">
            {status.kind === "added" ? (
              <span className="text-muted-foreground flex items-center gap-1 text-sm">
                <CheckIcon aria-hidden className="size-4" />
                {W.addedState}
              </span>
            ) : (
              <Button
                type="button"
                size="sm"
                variant={status.kind === "undo" ? "secondary" : "default"}
                data-act={status.kind === "undo" ? "undo" : "add"}
                data-endpoint={status.kind === "undo" ? `/api/undo/${status.tick.writeId}` : ACT_PATH}
                onClick={() => void (status.kind === "undo" ? undo() : add())}
              >
                {status.kind === "undo" ? <Undo2Icon aria-hidden /> : <CheckIcon aria-hidden />}
                {status.kind === "undo" ? W.undoBtn : W.addGo}
              </Button>
            )}
            {status.kind === "idle" && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
                {W.addCancel}
              </Button>
            )}
            {line && (
              <span role="status" className={cn("text-xs", line.bad ? "text-bad" : "text-muted-foreground")}>
                {line.text}
              </span>
            )}
          </div>
        </div>
      )}
    </li>
  );
}
