// The account list: one row per company with "13 people · 9 P1", a stage bar from
// `stage_counts` (brief: replaces the "P1 · 13" tags), the account tags, the industry and the
// goals. The company name is a Button that opens the drawer; the whole row also opens it.
// Hooks: `[data-row][data-account]`, `[data-people-label]`, `[data-stagebar]` (StageBar).
import { StageBar } from "@/components/saley/StageBar";
import { Tag } from "@/components/saley/Tag";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmt, W } from "@/words";
import { accountTag } from "./account-rules";
import type { Account } from "./types";

export function AccountTable({
  items,
  onOpen,
}: {
  items: Account[];
  onOpen: (account: Account, opener: HTMLElement | null) => void;
}) {
  return (
    <Table className="min-w-[44rem]">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[26%]">{W.colCompany}</TableHead>
          <TableHead className="w-[28%]">{W.colPeople}</TableHead>
          <TableHead>{W.colTags}</TableHead>
          <TableHead>{W.colIndustry}</TableHead>
          <TableHead>{W.colGoals}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((a) => (
          <TableRow
            key={a.id}
            data-row
            data-account={a.id}
            className="cursor-pointer"
            onClick={(e) => onOpen(a, e.currentTarget.querySelector<HTMLElement>("[data-open-account]"))}
          >
            <TableCell className="max-w-0 py-2.5">
              <Button
                type="button"
                variant="link"
                data-open-account
                aria-label={fmt(W.openCompany, { name: a.name })}
                className="text-foreground hover:text-primary h-auto max-w-full justify-start p-0 font-semibold"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpen(a, e.currentTarget);
                }}
              >
                <span className="truncate">{a.name}</span>
              </Button>
            </TableCell>
            <TableCell className="py-2.5">
              <div className="flex flex-col gap-1.5">
                <span data-people-label className="nums text-sm">
                  {a.people_label}
                </span>
                <StageBar counts={a.stage_counts} className="max-w-56" />
              </div>
            </TableCell>
            <TableCell className="py-2.5">
              <span className="flex flex-wrap gap-1">
                {a.tags.length === 0 && <span className="text-muted-foreground">—</span>}
                {a.tags.map((name) => {
                  const spec = accountTag(name);
                  return (
                    <Tag key={name} tag={spec.tag} tone={spec.tone}>
                      {name}
                    </Tag>
                  );
                })}
              </span>
            </TableCell>
            <TableCell className="text-muted-foreground py-2.5">{a.industry || "—"}</TableCell>
            <TableCell className="text-muted-foreground nums py-2.5">
              {a.goal_codes.length ? a.goal_codes.join(", ") : "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
