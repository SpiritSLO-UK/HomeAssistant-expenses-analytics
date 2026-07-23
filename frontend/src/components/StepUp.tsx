import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { isStepUpError, mfaStepUp } from "../api/client";

// Owner admin actions can be challenged for a fresh MFA code (#124/#157). This
// wraps the "run action → on step_up_required prompt for a code → replay the
// action" dance so callers don't reimplement it (Settings retention already does
// this by hand). Pair the returned state with <StepUpModal step={...} />.
//
//   const step = useStepUp();
//   const del = useMutation({ mutationFn: ..., onError: step.guard(showError) });
//   // trigger via step.run so it can be replayed after a successful step-up:
//   step.run(() => del.mutate());
export function useStepUp() {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const lastAction = useRef<(() => void) | null>(null);

  // Remember the action so it can be replayed once the step-up succeeds.
  const run = (action: () => void) => {
    lastAction.current = action;
    action();
  };
  // Use as a mutation's onError; non-step-up errors fall through to `fallback`.
  const guard = (fallback: (e: unknown) => void) => (e: unknown) => {
    if (isStepUpError(e)) {
      setOpen(true);
      return;
    }
    fallback(e);
  };
  const verify = useMutation({
    mutationFn: () => mfaStepUp(code),
    onSuccess: () => {
      setOpen(false);
      setCode("");
      setCodeError(null);
      lastAction.current?.();
    },
    onError: () => setCodeError("That code didn't match. Try again."),
  });
  const cancel = () => {
    setOpen(false);
    setCode("");
    setCodeError(null);
  };
  return { open, code, setCode, codeError, run, guard, verify, cancel };
}

export function StepUpModal({
  step,
  name,
}: Readonly<{ step: ReturnType<typeof useStepUp>; name?: string }>) {
  if (!step.open) return null;
  return (
    <div className="card" style={{ borderLeft: "3px solid #2d7", marginTop: 12 }}>
      <h2 className="card__title">🔐 Confirm it's you</h2>
      <p className="muted">
        This action needs a fresh two-factor code. Enter the current code — your last action will run
        automatically.
      </p>
      <form
        className="form-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (step.code) step.verify.mutate();
        }}
      >
        <input
          name={name ?? "mfa-stepup-code"}
          autoComplete="one-time-code"
          inputMode="numeric"
          autoFocus
          placeholder="123456"
          maxLength={8}
          value={step.code}
          onChange={(e) => step.setCode(e.target.value.replace(/\D/g, ""))}
          style={{ width: 120 }}
        />
        <button className="btn" type="submit" disabled={!step.code || step.verify.isPending}>
          {step.verify.isPending ? "Verifying…" : "Verify"}
        </button>
        <button className="btn btn--ghost" type="button" onClick={step.cancel}>
          Cancel
        </button>
      </form>
      {step.codeError && <p className="status status--error">{step.codeError}</p>}
    </div>
  );
}
