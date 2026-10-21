// D163: the theme follows the system by default. A click on the toggle sets light or dark and
// saves it in localStorage. Storage can be blocked (private window): then the choice lasts
// for this page only, and nothing breaks.
export type Theme = "light" | "dark";

const KEY = "saley.theme";

function saved(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function currentTheme(): Theme {
  return saved() ?? systemTheme();
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function saveTheme(theme: Theme): void {
  applyTheme(theme);
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    // Blocked storage: the theme still changes for this page.
  }
}

/** Follow system changes while the person has not picked a theme. */
export function followSystem(): () => void {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (saved() === null) applyTheme(systemTheme());
  };
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
