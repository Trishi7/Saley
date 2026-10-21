import { useSyncExternalStore } from "react";
import { parseHash, type Route } from "@/lib/route";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

const readHash = () => window.location.hash;

/** The current hash route. Re-renders on every hash change (links, Back, go()). */
export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, readHash);
  return parseHash(hash);
}
