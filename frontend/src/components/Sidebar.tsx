import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { NAV_ITEMS } from "../nav";
import { getAiStatus } from "../api/client";
import { getHiddenNavKeys, getNavOrder, setHiddenNavKeys, setNavOrder } from "../prefs";

const VERSION = "1.0.0-rc8";

// The footer badge reflects the live privacy posture: "Local-first" only holds
// while AI is off or on-device. A cloud AI mode sends data off the box, so the
// badge says so plainly rather than implying everything stays local.
function privacyBadge(ai?: { enabled: boolean; is_cloud: boolean }): { label: string; title: string } {
  if (!ai?.enabled) {
    return { label: "🔒 Local-first", title: "No AI is active — nothing leaves this device." };
  }
  if (ai.is_cloud) {
    return { label: "☁️ Cloud AI on", title: "A cloud AI mode is enabled — redacted payloads may be sent off-device. See Logs → Decisions." };
  }
  return { label: "🔒 Local-first · local AI", title: "A local (on-device) AI model is in use — nothing leaves this device." };
}

// Core pages that can never be hidden — you always need a way home and a way
// back to Settings (and Customise itself lives in the sidebar regardless). They
// can still be re-ordered; only hiding is locked.
const ALWAYS_SHOWN = new Set(["/", "/settings"]);

const ALL_PATHS = NAV_ITEMS.map((i) => i.path);

// Saved order may be stale across releases: keep known paths in their saved
// position, then append any nav items added since (or never ordered) at the end.
function mergeNavOrder(saved: string[]): string[] {
  const known = new Set(ALL_PATHS);
  const kept = saved.filter((p) => known.has(p));
  const keptSet = new Set(kept);
  return [...kept, ...ALL_PATHS.filter((p) => !keptSet.has(p))];
}

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
  const ai = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const badge = privacyBadge(ai.data);
  const [hidden, setHidden] = useState<Set<string>>(() => getHiddenNavKeys());
  const [order, setOrder] = useState<string[]>(() => mergeNavOrder(getNavOrder()));
  const [editing, setEditing] = useState(false);

  // Role visibility (child → allowance only; ownerOnly → admin). Unchanged.
  const roleItems = NAV_ITEMS.filter((item) => {
    if (isChild) return item.childVisible;
    return !item.ownerOnly || isAdmin;
  });

  // Apply the per-device order to the role-visible items.
  const orderedRoleItems = [...roleItems].sort((a, b) => order.indexOf(a.path) - order.indexOf(b.path));

  const toggle = (path: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      setHiddenNavKeys(next);
      return next;
    });

  // Move a tab up/down within the visible list, then persist the full order
  // (role-hidden paths, if any, are preserved at the end).
  const move = (path: string, dir: -1 | 1) => {
    const visible = orderedRoleItems.map((i) => i.path);
    const i = visible.indexOf(path);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= visible.length) return;
    [visible[i], visible[j]] = [visible[j], visible[i]];
    const visibleSet = new Set(visible);
    const next = [...visible, ...order.filter((p) => !visibleSet.has(p))];
    setOrder(next);
    setNavOrder(next);
  };

  // Normal mode drops hidden tabs; edit mode shows them all so they can be
  // re-enabled / re-ordered. The child role never customises (its one page).
  const items =
    isChild || editing ? orderedRoleItems : orderedRoleItems.filter((i) => !hidden.has(i.path));

  return (
    <aside className={"sidebar" + (open ? " sidebar--open" : "")}>
      <div className="sidebar__brand">
        <span className="sidebar__brand-icon">💷</span>
        <span className="sidebar__brand-text">Finance</span>
      </div>
      <nav className="sidebar__nav">
        {items.map((item, idx) =>
          editing ? (
            <EditRow
              key={item.path}
              item={item}
              shown={!hidden.has(item.path)}
              locked={ALWAYS_SHOWN.has(item.path)}
              canUp={idx > 0}
              canDown={idx < items.length - 1}
              onToggle={() => toggle(item.path)}
              onMoveUp={() => move(item.path, -1)}
              onMoveDown={() => move(item.path, 1)}
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
        {editing && (
          <p className="sidebar__hint">Use ▲ ▼ to reorder; 👁️ shows/hides. Hidden tabs stay reachable by URL.</p>
        )}
        <div title={badge.title}>{badge.label} · v{VERSION}</div>
      </div>
    </aside>
  );
}

function EditRow({
  item,
  shown,
  locked,
  canUp,
  canDown,
  onToggle,
  onMoveUp,
  onMoveDown,
}: Readonly<{
  item: { path: string; label: string; icon: string };
  shown: boolean;
  locked: boolean;
  canUp: boolean;
  canDown: boolean;
  onToggle: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}>) {
  const stateTitle = shown ? "Hide this tab" : "Show this tab";
  const toggleTitle = locked ? "Always shown" : stateTitle;
  const stateIcon = shown ? "👁️" : "🚫";
  return (
    <div className={"sidebar__link sidebar__link--edit" + (shown ? "" : " sidebar__link--off")}>
      <span className="sidebar__link-icon">{item.icon}</span>
      <span style={{ flex: 1, textAlign: "left" }}>{item.label}</span>
      <button type="button" className="sidebar__navbtn" disabled={!canUp} onClick={onMoveUp} title="Move up" aria-label={`Move ${item.label} up`}>
        ▲
      </button>
      <button type="button" className="sidebar__navbtn" disabled={!canDown} onClick={onMoveDown} title="Move down" aria-label={`Move ${item.label} down`}>
        ▼
      </button>
      <button type="button" className="sidebar__navbtn" disabled={locked} onClick={onToggle} title={toggleTitle} aria-label={toggleTitle}>
        {locked ? "🔒" : stateIcon}
      </button>
    </div>
  );
}
