import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  readonly children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Catches render-time errors anywhere below it and shows a recoverable fallback
 * instead of a blank white screen — previously a thrown render unmounted the whole
 * app with no way back (CR-FEAT-7). Local-first: the error is logged to the console
 * for debugging, never sent anywhere.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    globalThis.console?.error("Unhandled render error:", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
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
