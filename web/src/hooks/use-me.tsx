// Who is signed in, the role and the feature flags, from /api/me (one call per page load).
// D164: `features` is the one source for every "coming soon" badge; the server refuses the acts
// of an off feature with the same settings, so a badge and a refusal never disagree.
import { createContext, useContext, type ReactNode } from "react";
import type { ApiError } from "@/api";
import { useApi } from "@/hooks/use-api";

/** Feature names of /api/me.features (plan §3.6 "Feature flags"). */
export const FEATURES = [
  "find_people",
  "email_write",
  "speaker_match",
  "apollo",
  "mail_replies",
  "dms",
] as const;
export type Feature = (typeof FEATURES)[number];
export type Features = Record<Feature, boolean>;

export type TeamMember = { short: string; name: string; role: string };
export type Me = {
  email: string;
  role: string;
  dev: boolean;
  features: Features;
  people: TeamMember[];
};

type MeState = { me: Me | undefined; error: ApiError | null; reload: () => void };

const MeContext = createContext<MeState>({ me: undefined, error: null, reload: () => undefined });

export function MeProvider({ children }: { children: ReactNode }) {
  const { data, error, reload } = useApi<Me>("/api/me");
  return (
    <MeContext.Provider value={{ me: data, error, reload }}>{children}</MeContext.Provider>
  );
}

export function useMe(): MeState {
  return useContext(MeContext);
}

/** The flags, or null while /api/me loads (then no badge shows yet: never a wrong badge). */
export function useFeatures(): Features | null {
  return useContext(MeContext).me?.features ?? null;
}

export function isAdmin(me: Me | undefined): boolean {
  return me?.role === "admin";
}
