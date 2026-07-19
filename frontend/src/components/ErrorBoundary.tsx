import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  readonly children: ReactNode;
}
interface State {
  error: Error | null;
  reloading: boolean;
}

// A failed dynamic import (lazy route chunk) after a new version is deployed:
// the open tab references old hashed asset filenames that no longer exist, so
// the fetch 404s. Reloading once picks up the new index.html + current hashes.
const CHUNK_LOAD_ERROR =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Loading chunk \S+ failed/i;
// Guard against a reload loop: only auto-reload if we haven't already done so in
// the last few seconds (if the reload didn't fix it, fall through to the visible
// fallback instead of looping). A later deploy (outside the window) can recover.
const RELOAD_STAMP_KEY = "hafi-chunk-reload-at";
const RELOAD_WINDOW_MS = 10_000;

function isChunkLoadError(error: Error | null): boolean {
  return CHUNK_LOAD_ERROR.test(error?.message ?? "");
}

function mayAutoReload(): boolean {
  try {
    const last = Number(globalThis.sessionStorage?.getItem(RELOAD_STAMP_KEY) ?? 0);
    return !last || Date.now() - last > RELOAD_WINDOW_MS;
  } catch {
    return false; // no sessionStorage (private mode / SSR) → don't auto-reload
  }
}

/**
 * Catches render-time errors anywhere below it and shows a recoverable fallback
 * instead of a blank white screen — previously a thrown render unmounted the whole
 * app with no way back (CR-FEAT-7). Local-first: the error is logged to the console
 * for debugging, never sent anywhere.
 *
 * Special-cases a failed lazy-chunk fetch (common right after an update, when an
 * open tab still points at old asset hashes) by reloading once to self-heal.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, reloading: false };

  static getDerivedStateFromError(error: Error): State {
    // Show a brief "updating" state (not the scary error card) while the
    // auto-reload in componentDidCatch kicks in for a recoverable chunk error.
    return { error, reloading: isChunkLoadError(error) && mayAutoReload() };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    globalThis.console?.error("Unhandled render error:", error, info.componentStack);
    if (isChunkLoadError(error) && mayAutoReload()) {
      try {
        globalThis.sessionStorage?.setItem(RELOAD_STAMP_KEY, String(Date.now()));
      } catch {
        /* no sessionStorage — reload anyway, the timing guard just won't persist */
      }
      globalThis.location.reload();
    }
  }

  render(): ReactNode {
    const { error, reloading } = this.state;
    if (!error) return this.props.children;
    if (reloading) {
      return (
        <div className="page" style={{ maxWidth: 640, margin: "10vh auto" }}>
          <div className="card">
            <p className="muted">Loading the latest version…</p>
          </div>
        </div>
      );
    }
    return (
      <div className="page" style={{ maxWidth: 640, margin: "10vh auto" }}>
        <div className="card">
          <h1 className="page__title">Something went wrong</h1>
          <p className="status status--error">
            {error.message || "An unexpected error occurred."}
          </p>
          <p className="muted">
            The page hit an unexpected error. Your data is safe — reloading usually fixes it.
          </p>
          <button className="btn" onClick={() => globalThis.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
