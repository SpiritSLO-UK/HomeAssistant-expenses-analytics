import { useState } from "react";
import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "../nav";
import { getHiddenNavKeys, setHiddenNavKeys } from "../prefs";

// Core pages that can never be hidden — you always need a way home and a way
// back to Settings (and Customise itself lives in the sidebar regardless).
const ALWAYS_SHOWN = new Set(["/", "/settings"]);

export default function Sidebar({
  role = "owner",
  canManageTabs = true,
  open = false,
  onNavigate,
}: Readonly<{
  role?: string;
  canManageTabs?: boolean; // owner or a granted member may customise the nav tabs (#28 RBAC)
  open?: boolean; // drawer open on narrow screens
  onNavigate?: () => void; // close the drawer after picking a page (mobile)
}>) {
  const isAdmin = role === "owner";
  const isChild = role === "child";
  const [hidden, setHidden] = useState<Set<string>>(() => getHiddenNavKeys());
  const [editing, setEditing] = useState(false);

  // Role visibility (child → allowance only; ownerOnly → admin). Unchanged.
  const roleItems = NAV_ITEMS.filter((item) => {
    if (isChild) return item.childVisible;
    return !item.ownerOnly || isAdmin;
  });

  const toggle = (path: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      setHiddenNavKeys(next);
      return next;
    });

  // Normal mode drops hidden tabs; edit mode shows them all so they can be
  // re-enabled. The child role never hides anything (its one page).
  const items =
    isChild || editing ? roleItems : roleItems.filter((i) => !hidden.has(i.path));

  return (
    <aside className={"sidebar" + (open ? " sidebar--open" : "")}>
      <div className="sidebar__brand">
        <span className="sidebar__brand-icon">💷</span>
        <span className="sidebar__brand-text">Finance</span>
      </div>
      <nav className="sidebar__nav">
        {items.map((item) =>
          editing ? (
            <EditRow
              key={item.path}
              item={item}
              shown={!hidden.has(item.path)}
              locked={ALWAYS_SHOWN.has(item.path)}
              onToggle={() => toggle(item.path)}
            />
          ) : (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === "/"}
              onClick={() => onNavigate?.()}
              className={({ isActive }) =>
                "sidebar__link" + (isActive ? " sidebar__link--active" : "")
              }
            >
              <span className="sidebar__link-icon">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ),
        )}
      </nav>
      <div className="sidebar__footer">
        {!isChild && canManageTabs && (
          <button className="sidebar__customise" onClick={() => setEditing((v) => !v)}>
            {editing ? "✓ Done" : "✏️ Customise tabs"}
          </button>
        )}
        {editing && <p className="sidebar__hint">Hidden tabs stay reachable by their URL.</p>}
        <div>Local-first · v0.9.0-beta</div>
      </div>
    </aside>
  );
}

function EditRow({
  item,
  shown,
  locked,
  onToggle,
}: Readonly<{
  item: { path: string; label: string; icon: string };
  shown: boolean;
  locked: boolean;
  onToggle: () => void;
}>) {
  const stateTitle = shown ? "Hide this tab" : "Show this tab";
  const title = locked ? "Always shown" : stateTitle;
  const stateIcon = shown ? "👁️" : "🚫";
  return (
    <button
      type="button"
      className={"sidebar__link sidebar__link--edit" + (shown ? "" : " sidebar__link--off")}
      disabled={locked}
      onClick={onToggle}
      title={title}
    >
      <span className="sidebar__link-icon">{item.icon}</span>
      <span style={{ flex: 1, textAlign: "left" }}>{item.label}</span>
      <span aria-hidden="true">{locked ? "🔒" : stateIcon}</span>
    </button>
  );
}
