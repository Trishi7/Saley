// Today, part 5: the Ask card at the foot of Today (D119, D131, D186). One typed question goes
// to POST /api/ask; the answer is plain server text, shown as text (React escapes it), never
// HTML. Questions only, no action. A 429 says "Too many questions." (D186). No <form>: gate
// step 9 allows none, so Enter calls send() directly. The Ask POST is counted apart from the
// control presses (`ask_posts`, scripts/qa_browser/core.py).
import { SendIcon } from "lucide-react";
import { useRef, useState } from "react";
import { ApiError, postJson } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import type { AskAnswer } from "./types";

const ASK_PATH = "/api/ask";
const MAX_QUESTION = 300; // schemas.AskIn
const LINE_BY_STATUS: Record<number, string> = {
  0: W.errNetwork,
  422: W.askBad,
  429: W.askBusy,
};

export function AskCard() {
  const [text, setText] = useState("");
  const [state, setState] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  async function send() {
    const question = text.trim();
    if (busyRef.current) return;
    if (!question) {
      setState(W.askEmpty);
      return;
    }
    busyRef.current = true;
    setBusy(true);
    setState(W.askWorking);
    try {
      const got = await postJson<AskAnswer>(ASK_PATH, { text: question.slice(0, MAX_QUESTION) });
      setAnswer(got.text);
      setState(got.left_out > 0 ? fmt(W.askLeftOut, { n: got.left_out }) : "");
    } catch (err) {
      setAnswer(null);
      const status = err instanceof ApiError ? err.status : -1;
      setState(LINE_BY_STATUS[status] ?? W.askDown);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return (
    <section
      id="ask-card"
      aria-labelledby="ask-title"
      className="bg-card flex flex-col gap-3 rounded-lg border p-4"
    >
      <div className="flex flex-col">
        <h2 id="ask-title" className="text-base font-semibold">
          {W.askTitle}
        </h2>
        <p className="text-muted-foreground text-xs">{W.askHint}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <input
          id="ask-input"
          type="text"
          maxLength={MAX_QUESTION}
          autoComplete="off"
          value={text}
          placeholder={W.askPlaceholder}
          aria-label={W.askLabel}
          aria-describedby="ask-state"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void send();
            }
          }}
          className={cn(
            "border-input bg-background h-10 min-w-0 flex-1 basis-60 rounded-md border px-3 text-sm",
            "placeholder:text-muted-foreground outline-none",
            "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
          )}
        />
        <Button
          id="ask-send"
          type="button"
          data-act="ask"
          data-endpoint={ASK_PATH}
          disabled={busy}
          aria-busy={busy || undefined}
          onClick={() => void send()}
          className="h-10"
        >
          <SendIcon aria-hidden />
          {W.askSend}
        </Button>
      </div>
      <p id="ask-state" role="status" className="text-muted-foreground min-h-5 text-sm">
        {state}
      </p>
      {answer !== null && (
        <div
          id="ask-answer"
          className="bg-muted/50 rounded-md px-3 py-2 text-sm whitespace-pre-line text-pretty"
        >
          {answer}
        </div>
      )}
    </section>
  );
}
