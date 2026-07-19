// Pure list reordering: move the element at `from` to index `to`, returning a new
// array (the input is left untouched). Shared by the nav editor's drag + ▲▼ moves.
// A dedicated util (kept separate from the Rules.tsx copy on purpose) so this PR
// stays tight and the editor's reorder logic is unit-obvious.
export function reorder<T>(list: readonly T[], from: number, to: number): T[] {
  const next = list.slice();
  if (from < 0 || from >= next.length) return next;
  const [moved] = next.splice(from, 1);
  const target = Math.max(0, Math.min(to, next.length));
  next.splice(target, 0, moved);
  return next;
}
