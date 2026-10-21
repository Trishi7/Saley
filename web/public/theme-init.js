// D163: set the theme before the first paint, so a dark page never shows one light frame.
// A classic external script in <head>: the CSP allows script-src 'self' only, never inline.
// theme.ts keeps the same key and rule; this file only reads.
(function () {
  var dark = false;
  try {
    var saved = window.localStorage.getItem("saley.theme");
    dark = saved === "dark" || (saved !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  } catch {
    dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  if (dark) document.documentElement.classList.add("dark");
})();
