import { useMemo, useState } from "react";
import { getColumnWidths, setColumnWidths } from "./prefs";

export interface ColumnDef {
  key: string;
  /** Default width in px. */
  width: number;
  /** Set false for fixed-width columns (e.g. a checkbox) that shouldn't resize. */
  resizable?: boolean;
}

const MIN_WIDTH = 48;

/**
 * Per-device, draggable column widths for a table (backlog: resize table columns).
 * Returns the current widths (merged over the saved overrides), a `startResize`
 * handler to wire to a header drag handle, and `reset` to clear overrides.
 */
export function useResizableColumns(tableKey: string, columns: ColumnDef[]) {
  const defaults = useMemo(
    () => Object.fromEntries(columns.map((c) => [c.key, c.width])) as Record<string, number>,
    [columns],
  );
  const [widths, setWidths] = useState<Record<string, number>>(() => ({
    ...defaults,
    ...getColumnWidths(tableKey),
  }));

  function startResize(key: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = widths[key] ?? defaults[key] ?? 120;
    const onMove = (ev: MouseEvent) =>
      setWidths((prev) => ({ ...prev, [key]: Math.max(MIN_WIDTH, startW + (ev.clientX - startX)) }));
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      // Persist the final widths on this device.
      setWidths((prev) => {
        setColumnWidths(tableKey, prev);
        return prev;
      });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function reset() {
    setWidths({ ...defaults });
    setColumnWidths(tableKey, {});
  }

  return { widths, startResize, reset };
}
