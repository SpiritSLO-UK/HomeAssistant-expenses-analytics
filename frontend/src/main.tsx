import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { initTheme } from "./theme";
import "./styles.css";

initTheme();

// Statuses that are deterministic client-side errors: a bad request, a role/auth
// gate, or a locked database. These never self-heal on retry, so we surface them
// at once instead of hammering the backend. Everything else (a network blip, a
// cold/5xx backend, a 404/401 from a just-expired Home Assistant ingress session
// right after a long idle) can clear within a second or two.
const NON_RETRYABLE_STATUSES = new Set([400, 403, 422, 423]);

// Give transient failures a few attempts with backoff. This is what masks the
// classic "first click after the Pi sat idle failed with a 404/400, then worked
// after I clicked around" cold-start symptom: the initial request lands in the
// brief window where the ingress session / backend is not ready, and a retry a
// second later succeeds.
function retryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && NON_RETRYABLE_STATUSES.has(error.status)) return false;
  return failureCount < 3;
}

const queryClient = new QueryClient({
  // Surface background query failures instead of swallowing them. No toast system
  // yet, so log to the console (local-first — nothing leaves the device).
  queryCache: new QueryCache({
    onError: (error, query) => {
      globalThis.console?.error("Query failed:", query.queryKey, error);
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: retryQuery,
      // Briefly treat data as fresh so navigating between pages doesn't refetch
      // everything on arrival; mutations still invalidate their keys explicitly.
      staleTime: 30_000,
    },
  },
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error('Root element "#root" not found — index.html is missing <div id="root">.');

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </HashRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
