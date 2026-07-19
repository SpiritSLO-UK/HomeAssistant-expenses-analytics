import { NavLink, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { pathMatches, visibleGroupItems, type NavGroup, type NavVisibilityCtx, type NavRenderItem } from "../nav";
import { getAiStatus } from "../api/client";

// Injected at build time from package.json (vite define) so it can't drift.
const VERSION = __APP_VERSION__;

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

export default function Sidebar({
  groups,
  role = "owner",
  canManageTabs = true,
  blockedNavKeys = [],
  open = false,
  onNavigate,
}: Readonly<{
  groups: NavGroup[]; // resolved layout (default or the user's saved layout)
  role?: string;
  canManageTabs?: boolean; // owner or a granted member may see settings-gated pages (#28 RBAC)
  blockedNavKeys?: string[]; // pages the owner restricted for this user (#108)
  open?: boolean; // drawer open on narrow screens
  onNavigate?: () => void; // close the drawer after picking a page (mobile)
}>) {
  const ai = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus, staleTime: 60_000 });
  const badge = privacyBadge(ai.data);
  const location = useLocation();
  const ctx: NavVisibilityCtx = { role, canManageTabs, blockedNavKeys };

  return (
    <aside className={"sidebar" + (open ? " sidebar--open" : "")}>
      <div className="sidebar__brand">
        <span className="sidebar__brand-icon">💷</span>
        <span className="sidebar__brand-text">Finance</span>
      </div>
      <nav className="sidebar__nav">
        {groups.map((group) => {
          const members = visibleGroupItems(group, ctx);
          if (members.length === 0) return null; // whole group hidden (no visible members)
          // A single visible member (a standalone group, or a group gated down to
          // one) renders as a plain link to that page. Multiple members render as
          // a group header; the members themselves appear as page sub-tabs.
          if (members.length === 1) {
            return <MemberLink key={group.id} member={members[0]} onNavigate={onNavigate} />;
          }
          return (
            <GroupHeader
              key={group.id}
              group={group}
              members={members}
              currentPath={location.pathname}
              onNavigate={onNavigate}
            />
          );
        })}
      </nav>
      <div className="sidebar__footer">
        <div title={badge.title}>{badge.label} · v{VERSION}</div>
      </div>
    </aside>
  );
}

// A plain sidebar link to a single page (standalone group / single visible member).
function MemberLink({ member, onNavigate }: Readonly<{ member: NavRenderItem; onNavigate?: () => void }>) {
  return (
    <NavLink
      to={member.path}
      end={member.path === "/"}
      onClick={() => onNavigate?.()}
      className={({ isActive }) => "sidebar__link" + (isActive ? " sidebar__link--active" : "")}
    >
      <span className="sidebar__link-icon">{member.icon}</span>
      <span>{member.label}</span>
    </NavLink>
  );
}

// A multi-item group: one header row linking to its first visible member, marked
// active whenever the current route is any of the group's members (its sub-tabs
// switch between them on the page itself).
function GroupHeader({
  group,
  members,
  currentPath,
  onNavigate,
}: Readonly<{
  group: NavGroup;
  members: NavRenderItem[];
  currentPath: string;
  onNavigate?: () => void;
}>) {
  const first = members[0];
  const active = members.some((m) => pathMatches(m.path, currentPath));
  const icon = group.icon ?? first.icon;
  const label = group.label ?? first.label;
  return (
    <NavLink
      to={first.path}
      onClick={() => onNavigate?.()}
      className={"sidebar__link sidebar__group" + (active ? " sidebar__link--active" : "")}
    >
      <span className="sidebar__link-icon">{icon}</span>
      <span>{label}</span>
    </NavLink>
  );
}
