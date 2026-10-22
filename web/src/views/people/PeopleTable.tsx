// The people list: name (a Button that opens the drawer, with the P1 tag), company and title,
// the stage badge, the next step on one line (tooltip for the rest) and the newest touch.
// The whole row also opens the drawer. Hooks: `[data-row][data-person]`, `[data-open-person]`.
import { OneLine } from "@/components/saley/Links";
import { StageTag } from "@/components/saley/StageTag";
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
import { lastTouch } from "./people-rules";
import type { PersonRow } from "./types";

export function PeopleTable({
  items,
  onOpen,
}: {
  items: PersonRow[];
  onOpen: (person: PersonRow, opener: HTMLElement | null) => void;
}) {
  return (
    <Table className="min-w-[48rem] table-fixed">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[22%]">{W.colName}</TableHead>
          <TableHead className="w-[22%]">{W.colCompany}</TableHead>
          <TableHead className="w-[15%]">{W.colStage}</TableHead>
          <TableHead>{W.colNext}</TableHead>
          <TableHead className="w-[17%]">{W.colLastTouch}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((p) => (
          <TableRow
            key={p.id}
            data-row
            data-person={p.id}
            className="cursor-pointer"
            onClick={(e) => onOpen(p, e.currentTarget.querySelector<HTMLElement>("[data-open-person]"))}
          >
            <TableCell className="py-2.5">
              <span className="flex min-w-0 items-center gap-1.5">
                <Button
                  type="button"
                  variant="link"
                  data-open-person
                  aria-label={fmt(W.openPerson, { name: p.name })}
                  className="text-foreground hover:text-primary h-auto min-w-0 justify-start p-0 font-semibold"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(p, e.currentTarget);
                  }}
                >
                  <span className="truncate">{p.name}</span>
                </Button>
                {p.is_p1 && (
                  <Tag tag="p1" tone="warn" className="shrink-0">
                    {W.lgP1}
                  </Tag>
                )}
              </span>
            </TableCell>
            <TableCell className="text-muted-foreground py-2.5">
              <OneLine text={[p.company, p.title].filter(Boolean).join(" · ") || null} />
            </TableCell>
            <TableCell className="py-2.5">
              <StageTag stage={p.stage} className="max-w-full" />
            </TableCell>
            <TableCell className="py-2.5">
              <OneLine text={p.next_step} />
            </TableCell>
            <TableCell className="text-muted-foreground nums py-2.5 text-xs">
              <OneLine text={lastTouch(p.last_touches)} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
