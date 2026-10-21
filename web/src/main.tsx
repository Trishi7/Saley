// Entry of the dashboard v2 (R5 T10).
// D146: Radix inserts <style> tags (react-remove-scroll via react-style-singleton). The CSP
// allows a <style> only with this request's nonce, so the nonce is set BEFORE the first render.
import { setNonce } from "get-nonce";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { applyTheme, currentTheme, followSystem } from "./theme";

const PLACEHOLDER = "__CSP_NONCE__";

function pageNonce(): string | null {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]');
  const value = meta?.content ?? "";
  return value && value !== PLACEHOLDER ? value : null;
}

const nonce = pageNonce();
if (nonce) setNonce(nonce);
applyTheme(currentTheme());
followSystem();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
