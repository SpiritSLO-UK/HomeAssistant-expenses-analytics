import { type ReactNode } from "react";

// Shared list-row wrapper for the Budgets, Projects and Savings account lists.
// Gives every row the same chrome: a single-pixel divider using the themed
// --border token (replacing three divergent treatments, one of which was a
// hardcoded hex) and consistent vertical padding. `className` is passed through
// so pages can keep the marker classes their e2e selectors depend on (e.g.
// `.budget-row`). Presentational only.
export default function ListRow({
  className,
  children,
}: Readonly<{
  className?: string;
  children: ReactNode;
}>) {
  return (
    <div className={className} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      {children}
    </div>
  );
}
