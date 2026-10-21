// One Q4 goal card (brief: one uniform card): the goal header (text, value band, days left,
// state tag) -> the ring (won ÷ need, D159/D171) -> the people as a compact table (name ·
// company · stage badge · one-line note with a tooltip) -> the blockers list. Each list shows
// a few rows and a "+N more" Button that opens the rest in place.
// Hooks: `[data-goal]` on the card, `[data-ring]` (Ring), `[data-goal-people] [data-person]`,
// `[data-goal-blockers] [data-blocker]`, `[data-more]`.
import { useState } from "react";
import { OneLine } from "@/components/saley/Links";
import { Ring } from "@/components/saley/Ring";
import { StageTag } from "@/components/saley/StageTag";
import { Tag } from "@/components/saley/Tag";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useMe } from "@/hooks/use-me";
import { fmt, W } from "@/words";
import {
  BLOCKERS_SHOWN,
  daysLeftText,
  goalProgress,
  lateTag,
  ownerName,
  PEOPLE_SHOWN,
  ragTag,
  visibleRows,
} from "./goal-rules";
import { MoreButton } from "./MoreButton";
import type { Goal } from "./types";

export function GoalCard({ goal }: { goal: Goal }) {
  const rag = ragTag(goal.rag);
  return (
    <article
      data-goal={goal.code}
      aria-labelledby={`goal-${goal.id}`}
      className="bg-card flex min-w-0 flex-col rounded-lg border shadow-card"
    >
      <header className="flex items-start justify-between gap-4 border-b p-4">
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-muted-foreground tabular text-xs">
            {goal.code} · {goal.value_text || "—"} · {fmt(W.goalDue, { d: goal.due_label })}
          </p>
          <h2 id={`goal-${goal.id}`} className="text-base leading-snug font-semibold text-pretty">
            {goal.text}
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <Tag tag={rag.tag} tone={rag.tone}>
              {rag.text}
            </Tag>
            <span className="text-muted-foreground tabular text-xs" data-days-left>
              {daysLeftText(goal.days_left)}
            </span>
          </div>
          {goal.why && <p className="text-muted-foreground text-xs text-pretty">{goal.why}</p>}
        </div>
        <Ring progress={goalProgress(goal)} className="shrink-0" />
      </header>
      <GoalPeople goal={goal} />
      <GoalBlockers goal={goal} />
    </article>
  );
}

function GoalPeople({ goal }: { goal: Goal }) {
  const [open, setOpen] = useState(false);
  const { shown, hidden } = visibleRows(goal.active, PEOPLE_SHOWN, open);
  const listId = `goal-${goal.id}-people`;
  const notListed = goal.active_count - goal.active.length; // the server sends at most 8
  return (
    <section className="flex flex-col gap-2 border-b p-4" data-goal-people>
      <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {W.goalPeople}
      </h3>
      {goal.active.length === 0 ? (
        <p className="text-muted-foreground text-sm">{W.goalNoPeople}</p>
      ) : (
        <Table id={listId} className="table-fixed">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-8 w-[30%] px-2">{W.colName}</TableHead>
              <TableHead className="h-8 w-[24%] px-2">{W.colCompany}</TableHead>
              <TableHead className="h-8 w-[22%] px-2">{W.colStage}</TableHead>
              <TableHead className="h-8 px-2">{W.colNote}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((p) => (
              <TableRow key={p.id} data-person={p.id}>
                <TableCell className="px-2 py-1.5 font-medium">
                  <OneLine text={p.name} />
                </TableCell>
                <TableCell className="text-muted-foreground px-2 py-1.5">
                  <OneLine text={p.company} />
                </TableCell>
                <TableCell className="px-2 py-1.5">
                  <StageTag stage={p.stage} className="max-w-full" />
                </TableCell>
                <TableCell className="text-muted-foreground px-2 py-1.5">
                  <OneLine text={p.note} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <MoreButton hidden={hidden} open={open} onToggle={() => setOpen(!open)} controls={listId} />
      {open && notListed > 0 && (
        <p className="text-muted-foreground tabular text-xs">
          {fmt(W.goalMorePeople, { n: notListed })}
        </p>
      )}
    </section>
  );
}

function GoalBlockers({ goal }: { goal: Goal }) {
  const { me } = useMe();
  const [open, setOpen] = useState(false);
  const { shown, hidden } = visibleRows(goal.blockers, BLOCKERS_SHOWN, open);
  const listId = `goal-${goal.id}-blockers`;
  return (
    <section className="flex flex-col gap-2 p-4" data-goal-blockers>
      <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {W.goalBlockers}
      </h3>
      {goal.blockers.length === 0 ? (
        <p className="text-muted-foreground text-sm">{W.goalNoBlockers}</p>
      ) : (
        <ul id={listId} className="flex flex-col divide-y">
          {shown.map((b, i) => {
            const late = lateTag(b.late_by_days);
            return (
              <li
                key={`${b.short}-${i}`}
                data-blocker
                className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm"
              >
                <OneLine text={b.item} className="min-w-0 flex-1 basis-40" />
                <span className="text-muted-foreground text-xs">
                  {ownerName(b.owner, me?.people)}
                </span>
                {late ? (
                  <Tag tag={late.tag} tone={late.tone}>
                    {late.text}
                  </Tag>
                ) : (
                  <span className="text-muted-foreground text-xs">—</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <MoreButton hidden={hidden} open={open} onToggle={() => setOpen(!open)} controls={listId} />
    </section>
  );
}
