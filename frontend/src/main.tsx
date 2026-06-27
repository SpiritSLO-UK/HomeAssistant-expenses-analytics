import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { initTheme } from "./theme";
import "./styles.css";

initTheme();

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
      retry: 1,
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
