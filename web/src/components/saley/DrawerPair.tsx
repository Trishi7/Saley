// The company and person drawers of one page (R5 T15: Search, Suggestions, Money), the same
// pair and hand-off as Accounts (T14): a person in the company drawer opens the person drawer,
// and the focus goes back to the control that opened the first drawer (drawer-focus.ts).
import { useCallback, useState } from "react";
import { CompanyDrawer, type DrawerCompany } from "@/views/accounts/CompanyDrawer";
import { PersonDrawer, type DrawerPerson, type SavedEmail } from "./PersonDrawer";
import { useToast } from "./Toaster";

export function useDrawerPair() {
  const toast = useToast();
  const [company, setCompany] = useState<DrawerCompany | null>(null);
  const [person, setPerson] = useState<DrawerPerson | null>(null);
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const [handOff, setHandOff] = useState(false);

  const openPerson = useCallback((next: DrawerPerson, from: HTMLElement | null) => {
    setOpener(from);
    setHandOff(false);
    setPerson(next);
  }, []);
  const openCompany = useCallback((next: DrawerCompany, from: HTMLElement | null) => {
    setOpener(from);
    setHandOff(false);
    setCompany(next);
  }, []);
  const onSaved = useCallback(
    (saved: SavedEmail) => {
      setPerson(null);
      toast(saved.label);
    },
    [toast],
  );

  const drawers = (
    <>
      <CompanyDrawer
        company={company}
        onClose={() => setCompany(null)}
        returnTo={handOff ? null : opener}
        onOpenPerson={(p, name) => {
          setHandOff(true);
          setCompany(null);
          setPerson({ contactId: p.id, name: p.name, company: name });
        }}
      />
      <PersonDrawer person={person} onClose={() => setPerson(null)} onSaved={onSaved} returnTo={opener} />
    </>
  );
  return { openPerson, openCompany, drawers };
}
