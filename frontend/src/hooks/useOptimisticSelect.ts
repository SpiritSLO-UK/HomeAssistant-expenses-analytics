import { useCallback, useRef, useState } from "react";

/**
 * Optimistic value overlay for select-on-change controls that fire a mutation
 * (FE-8 / #247).
 *
 * A controlled `<select>` bound straight to a server value snaps back to the old
 * option the instant the user picks a new one — the new value only sticks once
 * the mutation succeeds and the query refetches. This overlay shows the chosen
 * value immediately and, if the mutation rejects, reverts to the server value and
 * surfaces the error.
 *
 * It overlays the *displayed* value locally rather than patching the query cache,
 * so it is cache-shape agnostic (works for flat lists and paginated caches alike)
 * and layers on top of a page's existing mutation without touching its
 * onSuccess / invalidation logic.
 *
 * Usage (one instance per select-kind; keyed by row id):
 * ```tsx
 * const cat = useOptimisticSelect<number, number | null>(surfaceError);
 * <select
 *   value={cat.valueFor(row.id, row.category_id ?? null) ?? ""}
 *   onChange={(e) => {
 *     const next = e.target.value ? Number(e.target.value) : null;
 *     cat.choose(row.id, next, () => setCategory.mutateAsync({ id: row.id, next }));
 *   }}
 * />
 * ```
 */
export interface OptimisticSelect<K, V> {
  /** Value to display for `key`: the pending choice if any, else `serverValue`. */
  valueFor: (key: K, serverValue: V) => V;
  /** Optimistically show `next` for `key`, run the mutation, revert on failure. */
  choose: (key: K, next: V, run: () => Promise<unknown>) => void;
}

export function useOptimisticSelect<K, V>(
  onError?: (error: unknown, key: K) => void,
): OptimisticSelect<K, V> {
  const overrides = useRef(new Map<K, V>());
  // Keep the latest onError without re-creating `choose` on every render.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const [, bump] = useState(0);
  const rerender = useCallback(() => bump((n) => n + 1), []);

  const valueFor = useCallback((key: K, serverValue: V): V => {
    const pending = overrides.current;
    if (!pending.has(key)) return serverValue;
    const chosen = pending.get(key) as V;
    if (Object.is(chosen, serverValue)) {
      // The server caught up to the choice — drop the override so a later
      // server-side change to this row still shows through. The values are
      // equal here, so dropping it causes no visible flash.
      pending.delete(key);
      return serverValue;
    }
    return chosen;
  }, []);

  const choose = useCallback(
    (key: K, next: V, run: () => Promise<unknown>): void => {
      overrides.current.set(key, next);
      rerender();
      run().catch((error: unknown) => {
        overrides.current.delete(key); // revert to the server value
        rerender();
        onErrorRef.current?.(error, key);
      });
    },
    [rerender],
  );

  return { valueFor, choose };
}
