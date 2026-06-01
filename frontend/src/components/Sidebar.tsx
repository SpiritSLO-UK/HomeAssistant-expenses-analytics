import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "../nav";

export default function Sidebar({ role = "owner" }: { role?: string }) {
  const isAdmin = role === "owner";
  const isChild = role === "child";
  const items = NAV_ITEMS.filter((item) => {
    if (isChild) return item.childVisible; // child sees only its allowance
    return !item.ownerOnly || isAdmin;
  });
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__brand-icon">💷</span>
        <span className="sidebar__brand-text">Finance</span>
      </div>
      <nav className="sidebar__nav">
        {items.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            end={item.path === "/"}
            className={({ isActive }) =>
              "sidebar__link" + (isActive ? " sidebar__link--active" : "")
            }
          >
            <span className="sidebar__link-icon">{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sidebar__footer">Local-first · v0.1.0</div>
    </aside>
  );
}
