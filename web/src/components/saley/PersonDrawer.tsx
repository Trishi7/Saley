// The person drawer (Appendix B hook `.drawer.on`). T13 built the Email field for an "Add
// email" task (D154); T14 adds the person: stage, Follow-up by (D31), links, papers, working
// on with its source, the touch timeline and the stage history (views/people/PersonDetails).
// One GET /api/people/{id} per opened person. The Email field shows while the person has no
// email on file (and at once for the "Add email" task, so the field can take the focus).
// Save is one POST /api/act with action `email_write`, the same body as the API tests: the
// server checks the address (D169) and that the cell is blank; its line shows here as it is.
// No <form>: gate step 9 allows no form and no submit button, so Enter calls save() directly.
import { MailIcon } from "lucide-react";
import { useRef, useState } from "react";
import { ApiError, postJson } from "@/api";
import { FeatureGate } from "@/components/saley/ComingSoon";
import { returnFocus, useLastShown } from "@/components/saley/drawer-focus";
import { StageTag } from "@/components/saley/StageTag";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useApi } from "@/hooks/use-api";
import { useFeatures } from "@/hooks/use-me";
import { cn } from "@/lib/utils";
import { DrawerSection } from "@/views/people/DrawerParts";
import { PersonDetails } from "@/views/people/PersonDetails";
import type { PersonData, PersonDetail } from "@/views/people/types";
import { fmt, W } from "@/words";

const ACT_PATH = "/api/act";
const EMAIL_MAX = 200; // D169: the cell limit (src/saley/web/act.py EMAIL_MAX)

export type DrawerPerson = {
  contactId: number;
  name: string | null;
  company: string | null;
  /** Open with the Email field focused (the "Add email" task). */
  focusEmail?: boolean;
};

export type SavedEmail = { contactId: number; label: string };

export function PersonDrawer({
  person: current,
  onClose,
  onSaved,
  returnTo,
}: {
  person: DrawerPerson | null;
  onClose: () => void;
  onSaved: (saved: SavedEmail) => void;
  /** The control that gets the focus back on close (see drawer-focus.ts). */
  returnTo?: HTMLElement | null;
}) {
  const open = current !== null;
  const person = useLastShown(current); // stays rendered while the sheet slides out
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        data-drawer="person"
        className={cn("drawer w-full gap-0 overflow-y-auto sm:max-w-lg", open && "on")}
        onCloseAutoFocus={(event) => returnFocus(event, returnTo)}
        onOpenAutoFocus={(event) => {
          // Focus the Email field, not the close button (brief: the click opens the field).
          const field = document.getElementById("drawer-email");
          if (person?.focusEmail && field instanceof HTMLInputElement && !field.disabled) {
            event.preventDefault();
            field.focus();
          }
        }}
      >
        {/* A new key per person: the field, its line and the answer start fresh for each. */}
        {person && <DrawerBody key={person.contactId} person={person} onSaved={onSaved} />}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  person,
  onSaved,
}: {
  person: DrawerPerson;
  onSaved: (saved: SavedEmail) => void;
}) {
  const { data, error, loading, reload } = useApi<PersonData>(`/api/people/${person.contactId}`);
  const item = data?.item;
  const needsEmail = person.focusEmail === true || (item !== undefined && !item.email);
  const sub = [item?.title, item?.company ?? person.company].filter(Boolean).join(" · ");
  return (
    <>
      <SheetHeader className="border-b pr-12">
        <SheetTitle className="text-lg text-balance">{item?.name ?? person.name ?? "—"}</SheetTitle>
        <SheetDescription>{sub || "—"}</SheetDescription>
        {item && <StageTag stage={item.stage} className="mt-1 w-fit" />}
      </SheetHeader>
      {needsEmail && <EmailField person={person} onSaved={onSaved} />}
      <div className="flex flex-col gap-6 p-4">
        {error && <ErrorLine what={W.whatPerson} error={error} onRetry={reload} />}
        {!item && loading && <LoadingLine />}
        {item?.email && <EmailOnFile item={item} />}
        {item && <PersonDetails item={item} />}
      </div>
    </>
  );
}

/** The email on file: a Button that opens the mail app (`mailto:` from the API, D39). */
function EmailOnFile({ item }: { item: PersonDetail }) {
  return (
    <DrawerSection title={W.emailLabel}>
      {item.email_href ? (
        <Button asChild variant="outline" size="sm" className="w-fit max-w-full">
          <a href={item.email_href} aria-label={fmt(W.openEmail, { email: item.email ?? "" })}>
            <MailIcon aria-hidden />
            <span className="truncate">{item.email}</span>
          </a>
        </Button>
      ) : (
        <span className="text-sm">{item.email}</span>
      )}
    </DrawerSection>
  );
}

function EmailField({
  person,
  onSaved,
}: {
  person: DrawerPerson;
  onSaved: (saved: SavedEmail) => void;
}) {
  const features = useFeatures();
  const off = features !== null && !features.email_write; // D154, D181
  const [value, setValue] = useState("");
  const [line, setLine] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false); // one click is one POST, also on a fast double click

  async function save() {
    const text = value.trim();
    if (busyRef.current || off) return;
    if (!text) {
      setLine(W.emailEmpty);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setLine("");
    try {
      const answer = await postJson<{ label: string }>(ACT_PATH, {
        action: "email_write",
        target_ids: [person.contactId],
        payload: { role: "email", value: text },
      });
      onSaved({ contactId: person.contactId, label: answer.label });
    } catch (err) {
      setLine(err instanceof ApiError ? err.message : W.errGeneric);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 border-b p-4" data-email-field>
      <label htmlFor="drawer-email" className="text-sm font-medium">
        {W.emailLabel}
      </label>
      <FeatureGate feature="email_write" className="items-start">
        <div className="flex w-full flex-wrap gap-2">
          <input
            id="drawer-email"
            type="email"
            inputMode="email"
            autoComplete="off"
            spellCheck={false}
            maxLength={EMAIL_MAX}
            disabled={off}
            value={value}
            placeholder={W.emailPlaceholder}
            aria-describedby="drawer-email-line"
            aria-invalid={line ? true : undefined}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void save();
              }
            }}
            className={cn(
              "border-input bg-background h-10 min-w-0 flex-1 basis-56 rounded-md border px-3 text-sm",
              "placeholder:text-muted-foreground outline-none",
              "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
          <Button
            type="button"
            data-act="email-save"
            data-endpoint={ACT_PATH}
            disabled={off || busy}
            aria-busy={busy || undefined}
            onClick={() => void save()}
            className="h-10"
          >
            {W.emailSave}
          </Button>
        </div>
      </FeatureGate>
      <p id="drawer-email-line" role="status" className="text-bad min-h-5 text-sm text-pretty">
        {line}
      </p>
      <p className="text-muted-foreground text-xs text-pretty">{W.emailHint}</p>
    </div>
  );
}
