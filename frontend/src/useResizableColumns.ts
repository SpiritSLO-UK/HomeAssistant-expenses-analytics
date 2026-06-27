import { useEffect, useMemo, useRef, useState } from "react";
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
 * handler to wire to a header drag handle (mouse or touch), and `reset` to clear
 * overrides.
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

  // Mirror the latest widths in a ref so the drag-end handler can persist them once,
  // without a setState-updater side effect (that ran twice under StrictMode and
  // double-wrote localStorage).
  const latest = useRef(widths);
  latest.current = widths;

  // Cleanup for an in-flight drag, invoked on unmount too, so a mid-drag unmount
  // can't leak window listeners or leave the page's text unselectable.
  const activeCleanup = useRef<null | (() => void)>(null);
  useEffect(() => () => activeCleanup.current?.(), []);

  function startResize(key: string, e: React.MouseEvent | React.TouchEvent) {
    e.preventDefault();
    e.stopPropagation();
    const startX = "touches" in e ? (e.touches[0]?.clientX ?? 0) : e.clientX;
    const startW = widths[key] ?? defaults[key] ?? 120;

    const apply = (clientX: number) => {
      const next = { ...latest.current, [key]: Math.max(MIN_WIDTH, startW + (clientX - startX)) };
      latest.current = next;
      setWidths(next);
    };

    // Collect listeners so cleanup can remove exactly what was added (no forward refs).
    const listeners: Array<[string, EventListener]> = [];
    const cleanup = () => {
      for (const [type, handler] of listeners) globalThis.removeEventListener(type, handler);
      document.body.style.userSelect = "";
      activeCleanup.current = null;
    };
    const onEnd = () => {
      cleanup();
      setColumnWidths(tableKey, latest.current); // persist once, on drag end
    };
    const add = (type: string, handler: EventListener, opts?: AddEventListenerOptions) => {
      globalThis.addEventListener(type, handler, opts);
      listeners.push([type, handler]);
    };

    add("mousemove", (ev) => apply((ev as MouseEvent).clientX));
    add("mouseup", onEnd);
    add("touchmove", (ev) => {
      const t = (ev as TouchEvent).touches[0];
      if (t) {
        ev.preventDefault(); // stop the page scrolling while dragging the handle
        apply(t.clientX);
      }
    }, { passive: false });
    add("touchend", onEnd);

    document.body.style.userSelect = "none"; // don't select text while dragging
    activeCleanup.current = cleanup;
  }

  function reset() {
    setWidths({ ...defaults });
    setColumnWidths(tableKey, {});
  }

  return { widths, startResize, reset };
}
