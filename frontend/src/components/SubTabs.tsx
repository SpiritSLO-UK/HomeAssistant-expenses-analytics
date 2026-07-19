import { useRef, type KeyboardEvent } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { pathMatches } from "../nav";

// A presentational horizontal tab strip (an ARIA tablist). Two modes so it can be
// reused for both grouped-nav page sub-tabs (router mode) and in-page section
// switches (controlled mode, e.g. the PR4 Settings sections):
//   - router mode:     tabs carry a `to`; the active tab is the one whose route
//                      matches the current location; each renders a <NavLink>.
//   - controlled mode: the parent owns `active` + `onChange`; each renders a
//                      <button> that reports its key on click.
// Both are keyboard-navigable (arrow / Home / End move focus across the tabs).

export interface RouterSubTab {
  key: string;
  label: string;
  to: string;
  icon?: string;
}

export interface ControlledSubTab {
  key: string;
  label: string;
  icon?: string;
}

type SubTabsProps =
  | Readonly<{
      mode?: "router";
      tabs: RouterSubTab[];
      ariaLabel?: string;
      onNavigate?: () => void;
    }>
  | Readonly<{
      mode: "controlled";
      tabs: ControlledSubTab[];
      active: string;
      onChange: (key: string) => void;
      ariaLabel?: string;
    }>;

// Icon (optional) + label, shared by both tab renderings.
function tabLabel(t: { label: string; icon?: string }) {
  return (
    <>
      {t.icon && <span className="subtabs__icon">{t.icon}</span>}
      <span>{t.label}</span>
    </>
  );
}

// Roving-focus helper: move keyboard focus between the tabs (wrapping around).
function focusTabAt(container: HTMLElement | null, index: number): void {
  const tabs = container?.querySelectorAll<HTMLElement>('[role="tab"]');
  if (!tabs || tabs.length === 0) return;
  const clamped = ((index % tabs.length) + tabs.length) % tabs.length;
  tabs[clamped]?.focus();
}

export default function SubTabs(props: SubTabsProps) {
  const location = useLocation();
  const ref = useRef<HTMLDivElement>(null);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const tabs = ref.current?.querySelectorAll<HTMLElement>('[role="tab"]');
    if (!tabs || tabs.length === 0) return;
    const current = Array.from(tabs).indexOf(document.activeElement as HTMLElement);
    const moves: Record<string, number> = {
      ArrowRight: current + 1,
      ArrowDown: current + 1,
      ArrowLeft: current - 1,
      ArrowUp: current - 1,
      Home: 0,
      End: tabs.length - 1,
    };
    if (!(e.key in moves)) return;
    e.preventDefault();
    focusTabAt(ref.current, moves[e.key]);
  };

  if (props.mode === "controlled") {
    return (
      <div className="subtabs" role="tablist" aria-label={props.ariaLabel} ref={ref} onKeyDown={onKeyDown}>
        {props.tabs.map((t) => {
          const selected = t.key === props.active;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={selected}
              tabIndex={selected ? 0 : -1}
              className={"subtabs__tab" + (selected ? " subtabs__tab--active" : "")}
              onClick={() => props.onChange(t.key)}
            >
              {tabLabel(t)}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="subtabs" role="tablist" aria-label={props.ariaLabel} ref={ref} onKeyDown={onKeyDown}>
      {props.tabs.map((t) => {
        const selected = pathMatches(t.to, location.pathname);
        return (
          <NavLink
            key={t.key}
            to={t.to}
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => props.onNavigate?.()}
            className={"subtabs__tab" + (selected ? " subtabs__tab--active" : "")}
          >
            {tabLabel(t)}
          </NavLink>
        );
      })}
    </div>
  );
}
