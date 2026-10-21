// ESLint flat config (R5 T10). plan §3.3: every visible string comes from src/words.ts,
// so the STE lint (saley.qa.ste_lint) sees all of it. react/jsx-no-literals enforces that.
import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// Separators only. Words never go here.
const ALLOWED = ["·", "—", ":", "/", "%", "+"];
// Attributes a person reads or hears. A plain string in one of them is an error: it must come
// from W. Other attributes (className, type, data-*) hold code words, not text. The rule's own
// noAttributeStrings flags className too, so this list takes its place.
const TEXT_ATTRS =
  "/^(aria-label|aria-description|aria-placeholder|aria-roledescription|title|placeholder|alt|label)$/";
const textAttr = (inner) => ({
  selector: `JSXAttribute[name.name=${TEXT_ATTRS}] ${inner}`,
  message: "Visible text comes from W in src/words.ts (plan §3.3).",
});

export default tseslint.config(
  { ignores: ["node_modules", "../src/saley/web/dist"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    settings: { react: { version: "19.3" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react/jsx-no-literals": [
        "error",
        { noStrings: true, noAttributeStrings: false, allowedStrings: ALLOWED, ignoreProps: true },
      ],
      "no-restricted-syntax": [
        "error",
        textAttr("> Literal"),
        textAttr("> JSXExpressionContainer > Literal"),
        textAttr("> JSXExpressionContainer > TemplateLiteral"),
      ],
      "no-eval": "error",
      "no-implied-eval": "off",
      "@typescript-eslint/no-implied-eval": "off",
      "no-new-func": "error",
    },
  },
  {
    // Vendored bklit helpers (D148): kept as upstream wrote them, so a later update is a plain
    // copy. Two React Compiler advisories are off here only. The text rules stay on.
    files: ["src/components/charts/use-enter-complete.ts", "src/components/charts/use-mount-progress.ts"],
    rules: { "react-hooks/set-state-in-effect": "off", "react-hooks/refs": "off" },
  },
  {
    files: ["scripts/**/*.mjs", "*.config.{js,ts}", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
  {
    // D163: a classic browser script loaded before the bundle (no modules, no imports).
    files: ["public/**/*.js"],
    languageOptions: { globals: globals.browser, sourceType: "script" },
  },
);
