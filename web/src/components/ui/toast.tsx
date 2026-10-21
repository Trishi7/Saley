// Toast: Radix Toast styled like shadcn (vendored as source, D148).
// Why Radix and not sonner: sonner inserts its own <style> tag without the CSP nonce, so a
// strict style-src blocks it and logs a console error (D146, zero console errors). Radix Toast
// inserts no style. The page has one toast region with id="toast" (docs/08 Appendix B hooks).
import { XIcon } from "lucide-react";
import { Toast as ToastPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";
import { W } from "@/words";

const ToastProvider = ToastPrimitive.Provider;

function ToastViewport({ className, ...props }: React.ComponentProps<typeof ToastPrimitive.Viewport>) {
  return (
    <ToastPrimitive.Viewport
      id="toast"
      data-slot="toast-viewport"
      className={cn(
        "fixed right-0 bottom-0 z-[100] flex max-h-screen w-full flex-col gap-2 p-4 outline-none sm:max-w-[420px]",
        className,
      )}
      {...props}
    />
  );
}

function Toast({
  className,
  tone = "default",
  ...props
}: React.ComponentProps<typeof ToastPrimitive.Root> & { tone?: "default" | "error" }) {
  return (
    <ToastPrimitive.Root
      data-slot="toast"
      data-tone={tone}
      className={cn(
        "bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-80 data-[state=open]:slide-in-from-bottom-full relative flex w-full items-start gap-3 rounded-md border p-4 pr-8 shadow-lg",
        tone === "error" && "border-destructive/50 text-destructive",
        className,
      )}
      {...props}
    />
  );
}

function ToastTitle({ className, ...props }: React.ComponentProps<typeof ToastPrimitive.Title>) {
  return (
    <ToastPrimitive.Title
      data-slot="toast-title"
      className={cn("text-sm font-semibold", className)}
      {...props}
    />
  );
}

function ToastDescription({
  className,
  ...props
}: React.ComponentProps<typeof ToastPrimitive.Description>) {
  return (
    <ToastPrimitive.Description
      data-slot="toast-description"
      className={cn("text-sm opacity-90", className)}
      {...props}
    />
  );
}

function ToastClose({ className, ...props }: React.ComponentProps<typeof ToastPrimitive.Close>) {
  return (
    <ToastPrimitive.Close
      data-slot="toast-close"
      aria-label={W.close}
      className={cn(
        "absolute top-2 right-2 rounded-md p-1 opacity-70 transition-opacity hover:opacity-100 focus:ring-2 focus:outline-none",
        className,
      )}
      {...props}
    >
      <XIcon className="size-4" />
    </ToastPrimitive.Close>
  );
}

export { Toast, ToastClose, ToastDescription, ToastProvider, ToastTitle, ToastViewport };
