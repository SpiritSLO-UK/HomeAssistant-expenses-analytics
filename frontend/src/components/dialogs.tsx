import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * Promise-based in-app dialog system, replacing the native window.confirm /
 * prompt / alert everywhere (FE-10).
 *
 * Native dialogs are blocking, poor on mobile/a11y, and — critically — a
 * sandboxed Home-Assistant ingress iframe can SUPPRESS `confirm`, which then
 * silently returns `false`. That quietly denied the cloud-AI approval gate. So
 * every native call is routed through this system instead.
 *
 * React callers use the hooks: `useConfirm()`, `usePrompt()`, `useAlert()`.
 * Non-React modules (the AI-approval gate in `lib/aiSuggest`) can't use hooks,
 * so they call the module-level `confirmAsync` / `alertAsync` singletons that
 * the mounted <DialogProvider> registers into. IMPORTANT: with no provider
 * registered, `confirmAsync` FAILS CLOSED (resolves `false`) — it never falls
 * back to native `confirm`, which would reintroduce the iframe bug.
 */

export interface ConfirmOptions {
  title?: string;
  message?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm button as destructive (red). */
  danger?: boolean;
}

export interface PromptOptions extends ConfirmOptions {
  defaultValue?: string;
  placeholder?: string;
}

export type AlertOptions = Pick<ConfirmOptions, "title" | "message" | "confirmLabel">;

type DialogKind = "confirm" | "prompt" | "alert";
type DialogResult = boolean | string | null | undefined;
type AllOptions = ConfirmOptions & PromptOptions & AlertOptions;

interface DialogRequest {
  id: number;
  kind: DialogKind;
  opts: AllOptions;
  resolve: (value: DialogResult) => void;
}

interface DialogApi {
  confirm: (opts?: ConfirmOptions) => Promise<boolean>;
  prompt: (opts?: PromptOptions) => Promise<string | null>;
  alert: (opts?: AlertOptions) => Promise<void>;
}

// --- module-level singleton for non-React callers (the AI gate) ---------------

type ConfirmFn = (opts?: ConfirmOptions) => Promise<boolean>;
type AlertFn = (opts?: AlertOptions) => Promise<void>;

let registeredConfirm: ConfirmFn | null = null;
let registeredAlert: AlertFn | null = null;

/** Register the mounted provider's confirm handler; returns an unregister fn. */
export function registerConfirm(fn: ConfirmFn): () => void {
  registeredConfirm = fn;
  return () => {
    if (registeredConfirm === fn) registeredConfirm = null;
  };
}

/** Register the mounted provider's alert handler; returns an unregister fn. */
export function registerAlert(fn: AlertFn): () => void {
  registeredAlert = fn;
  return () => {
    if (registeredAlert === fn) registeredAlert = null;
  };
}

/**
 * Confirm from a non-React module. FAILS CLOSED: if no provider is registered
 * yet, resolves `false` rather than falling back to native `confirm` (which a
 * sandboxed ingress iframe can suppress). The AI-approval gate depends on this.
 */
export function confirmAsync(opts?: ConfirmOptions): Promise<boolean> {
  if (!registeredConfirm) return Promise.resolve(false);
  return registeredConfirm(opts);
}

/** Alert from a non-React module. No-ops if no provider is registered. */
export function alertAsync(opts?: AlertOptions): Promise<void> {
  if (!registeredAlert) return Promise.resolve();
  return registeredAlert(opts);
}

// --- context + hooks ----------------------------------------------------------

const DialogContext = createContext<DialogApi | null>(null);

function useDialogs(): DialogApi {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("Dialog hooks must be used inside <DialogProvider>.");
  return ctx;
}

export function useConfirm(): DialogApi["confirm"] {
  return useDialogs().confirm;
}
export function usePrompt(): DialogApi["prompt"] {
  return useDialogs().prompt;
}
export function useAlert(): DialogApi["alert"] {
  return useDialogs().alert;
}

// --- provider -----------------------------------------------------------------

export function DialogProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [queue, setQueue] = useState<DialogRequest[]>([]);
  const idRef = useRef(0);

  const enqueue = useCallback(
    (kind: DialogKind, opts: AllOptions) =>
      new Promise<DialogResult>((resolve) => {
        idRef.current += 1;
        setQueue((q) => [...q, { id: idRef.current, kind, opts, resolve }]);
      }),
    [],
  );

  const confirm = useCallback(
    (opts: ConfirmOptions = {}) => enqueue("confirm", opts).then((v) => v === true),
    [enqueue],
  );
  const prompt = useCallback(
    (opts: PromptOptions = {}) =>
      enqueue("prompt", opts).then((v) => (typeof v === "string" ? v : null)),
    [enqueue],
  );
  const alert = useCallback(
    (opts: AlertOptions = {}) => enqueue("alert", opts).then(() => undefined),
    [enqueue],
  );

  // Register the singletons the non-React AI gate calls into, while mounted.
  useEffect(() => {
    const unConfirm = registerConfirm(confirm);
    const unAlert = registerAlert(alert);
    return () => {
      unConfirm();
      unAlert();
    };
  }, [confirm, alert]);

  const api = useMemo<DialogApi>(() => ({ confirm, prompt, alert }), [confirm, prompt, alert]);

  const dismiss = useCallback((id: number) => {
    setQueue((q) => q.filter((r) => r.id !== id));
  }, []);

  const active = queue[0];
  return (
    <DialogContext.Provider value={api}>
      {children}
      {active && <DialogHost key={active.id} request={active} onDismiss={dismiss} />}
    </DialogContext.Provider>
  );
}

// --- the rendered modal -------------------------------------------------------

const DEFAULT_TITLE: Record<DialogKind, string> = {
  confirm: "Confirm",
  prompt: "Enter a value",
  alert: "Notice",
};

function DialogHost({
  request,
  onDismiss,
}: Readonly<{ request: DialogRequest; onDismiss: (id: number) => void }>) {
  const { id, kind, opts } = request;
  const dialogRef = useRef<HTMLDialogElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(opts.defaultValue ?? "");

  useEffect(() => {
    const dlg = dialogRef.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dlg?.showModal();
    // Focus the text field for a prompt, otherwise the primary button.
    if (kind === "prompt") {
      inputRef.current?.focus();
      inputRef.current?.select();
    } else {
      confirmBtnRef.current?.focus();
    }
    return () => {
      dlg?.close();
      // Restore focus to whatever was focused before the dialog opened.
      previouslyFocused?.focus?.();
    };
  }, [kind]);

  const finish = (result: DialogResult) => {
    request.resolve(result);
    onDismiss(id);
  };
  const cancel = () => {
    if (kind === "alert") finish(undefined);
    else finish(kind === "prompt" ? null : false);
  };
  const accept = () => {
    if (kind === "prompt") finish(value);
    else if (kind === "alert") finish(undefined);
    else finish(true);
  };

  const titleId = `dialog-title-${id}`;
  const confirmClass = opts.danger ? "btn btn--danger" : "btn";

  return (
    <dialog
      ref={dialogRef}
      className="modal-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault(); // Esc → treat as cancel, not a silent close
        cancel();
      }}
    >
      <div className="card" style={{ maxWidth: 480, margin: 0 }}>
        <h2 className="card__title" id={titleId}>
          {opts.title ?? DEFAULT_TITLE[kind]}
        </h2>
        {opts.message != null && (
          <p className="muted" style={{ marginTop: 0, whiteSpace: "pre-wrap" }}>
            {opts.message}
          </p>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            accept();
          }}
        >
          {kind === "prompt" && (
            <div className="field">
              <input
                ref={inputRef}
                value={value}
                placeholder={opts.placeholder}
                onChange={(e) => setValue(e.target.value)}
              />
            </div>
          )}
          <div className="form-row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
            {kind !== "alert" && (
              <button type="button" className="btn btn--ghost" onClick={cancel}>
                {opts.cancelLabel ?? "Cancel"}
              </button>
            )}
            <button ref={confirmBtnRef} type="submit" className={confirmClass}>
              {opts.confirmLabel ?? "OK"}
            </button>
          </div>
        </form>
      </div>
    </dialog>
  );
}
