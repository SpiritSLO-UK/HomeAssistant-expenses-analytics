import { useRef, useState, type Dispatch, type SetStateAction } from "react";

/**
 * Local form state seeded from a server value that RE-SYNCS when that value changes.
 *
 * A plain `useState(serverValue)` only seeds on mount, so a controlled input goes
 * stale after its query refetches with a new value (FE-7 — e.g. an interest rate or
 * holding still shows the old number after a save). This adopts the server value
 * only when it actually changes between renders (React's "adjust state during
 * render" pattern), so it stays editable and doesn't clobber typing on unrelated
 * re-renders.
 */
export function useServerState<T>(serverValue: T): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(serverValue);
  const prev = useRef<T>(serverValue);
  if (prev.current !== serverValue) {
    prev.current = serverValue;
    setValue(serverValue);
  }
  return [value, setValue];
}
