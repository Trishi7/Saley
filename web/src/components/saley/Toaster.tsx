// The one toast region of the page (`#toast`, Appendix B) and a hook to show a line in it.
// Radix Toast, not sonner (D189: no <style> without the nonce). Used for short answers that
// need no action, e.g. "This is <name>'s commitment. Only they can mark it done." (D155, T16).
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import {
  Toast,
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastViewport,
} from "@/components/ui/toast";

type Tone = "default" | "error";
type Item = { id: number; line: string; tone: Tone };
type Show = (line: string, tone?: Tone) => void;

const ToastContext = createContext<Show>(() => undefined);
const SHOW_MS = 6000;
let nextId = 1;

export function Toaster({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const show = useCallback<Show>((line, tone = "default") => {
    const id = nextId++;
    setItems((list) => [...list, { id, line, tone }]);
  }, []);
  const drop = useCallback((id: number) => {
    setItems((list) => list.filter((item) => item.id !== id));
  }, []);
  return (
    <ToastContext.Provider value={show}>
      <ToastProvider duration={SHOW_MS}>
        {children}
        {items.map((item) => (
          <Toast
            key={item.id}
            tone={item.tone}
            data-toast-line
            onOpenChange={(open) => {
              if (!open) drop(item.id);
            }}
          >
            <ToastDescription>{item.line}</ToastDescription>
            <ToastClose />
          </Toast>
        ))}
        <ToastViewport />
      </ToastProvider>
    </ToastContext.Provider>
  );
}

/** Show one line in the toast region. */
export function useToast(): Show {
  return useContext(ToastContext);
}
