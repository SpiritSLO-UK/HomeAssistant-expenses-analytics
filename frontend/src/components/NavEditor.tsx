import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  defaultIconForPath,
  defaultLabelForPath,
  isCustomGroupId,
  pathPassesGate,
  resolveLayout,
  toNavLayout,
  DEFAULT_NAV_GROUPS,
  type NavGroup,
  type NavVisibilityCtx,
} from "../nav";
import { resetNavLayout, saveNavLayout, type NavLayout } from "../api/client";
import { reorder } from "../lib/reorder";
import { getHiddenNavKeys, getNavOrder } from "../prefs";

// The interactive "Customise navigation" editor (grouped-nav PR3/4). Operates on a
// working copy of the resolved layout; every change is debounced-saved through the
// PR1 self-service API and the ["me"] query is invalidated so the live sidebar +
// sub-tabs reflect the edit. Any approved non-child user may open it — this is a
// personal preference, not an owner/settings-gated action.

type SaveState = "idle" | "saving" | "saved" | "error";

// --- pure layout-mutation helpers (module scope keeps the component thin) ------

function cloneGroups(groups: NavGroup[]): NavGroup[] {
  return groups.map((g) => ({ ...g, items: g.items.map((it) => ({ ...it })) }));
}

// A stable, unguessable id for a user-created group. Uses the CSPRNG (not
// Math.random) so ids never collide across quick successive creates.
function customGroupId(): string {
  const buf = new Uint8Array(6);
  globalThis.crypto.getRandomValues(buf);
  const hex = Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
  return `custom:${hex}`;
}

// Keep only gate-passing members so the editor can never surface a page this user
// can't access. Hidden (but accessible) members are retained so they stay re-showable.
function gateFilter(groups: NavGroup[], ctx: NavVisibilityCtx): NavGroup[] {
  return groups.map((g) => ({ ...g, items: g.items.filter((it) => pathPassesGate(it.path, ctx)) }));
}

function renameGroup(groups: NavGroup[], id: string, label: string): NavGroup[] {
  return groups.map((g) => (g.id === id ? { ...g, label: label || undefined } : g));
}

function setGroupIcon(groups: NavGroup[], id: string, icon: string): NavGroup[] {
  return groups.map((g) => (g.id === id ? { ...g, icon: icon || undefined } : g));
}

function moveGroup(groups: NavGroup[], from: number, to: number): NavGroup[] {
  return reorder(groups, from, to);
}

// Delete a (custom) group: drop it, then re-resolve so its orphaned members fall
// back to their default group rather than being lost (relies on resolveLayout's
// missing-path re-append). Gate-filtered again to stay within this user's pool.
function deleteGroup(groups: NavGroup[], id: string, ctx: NavVisibilityCtx): NavGroup[] {
  const remaining = groups.filter((g) => g.id !== id);
  return gateFilter(resolveLayout(toNavLayout(remaining)), ctx);
}

function addCustomGroup(groups: NavGroup[], label: string, icon: string): NavGroup[] {
  return [...groups, { id: customGroupId(), label, icon: icon || undefined, items: [] }];
}

function renameItem(groups: NavGroup[], gid: string, path: string, label: string): NavGroup[] {
  return groups.map((g) =>
    g.id === gid
      ? { ...g, items: g.items.map((it) => (it.path === path ? { ...it, label: label || undefined } : it)) }
      : g,
  );
}

function toggleHiddenItem(groups: NavGroup[], gid: string, path: string): NavGroup[] {
  return groups.map((g) =>
    g.id === gid
      ? { ...g, items: g.items.map((it) => (it.path === path ? { ...it, hidden: !it.hidden } : it)) }
      : g,
  );
}

function moveItemWithin(groups: NavGroup[], gid: string, from: number, to: number): NavGroup[] {
  return groups.map((g) => (g.id === gid ? { ...g, items: reorder(g.items, from, to) } : g));
}

// Move a member out of `fromGid` and append it to `toGid`.
function moveItemToGroup(groups: NavGroup[], fromGid: string, path: string, toGid: string): NavGroup[] {
  if (fromGid === toGid) return groups;
  const moved = groups.find((g) => g.id === fromGid)?.items.find((it) => it.path === path);
  if (!moved) return groups;
  return groups.map((g) => {
    if (g.id === fromGid) return { ...g, items: g.items.filter((it) => it.path !== path) };
    if (g.id === toGid) return { ...g, items: [...g.items, moved] };
    return g;
  });
}

// Human title for a group in the editor + the move-to-group control.
function groupTitle(group: NavGroup): string {
  if (group.label) return group.label;
  const first = group.items[0];
  return first ? defaultLabelForPath(first.path) : group.id;
}

// --- actions bag passed down to the row components -----------------------------

interface EditorActions {
  ctx: NavVisibilityCtx;
  groups: NavGroup[];
  renameGroup: (id: string, label: string) => void;
  setGroupIcon: (id: string, icon: string) => void;
  moveGroupBy: (index: number, delta: number) => void;
  deleteGroup: (id: string) => void;
  renameItem: (gid: string, path: string, label: string) => void;
  toggleHidden: (gid: string, path: string) => void;
  moveItemBy: (gid: string, index: number, delta: number) => void;
  moveItemTo: (fromGid: string, path: string, toGid: string) => void;
  dragItem: MutableRefObject<{ gid: string; path: string } | null>;
  dragGroup: MutableRefObject<string | null>;
}

// --- component -----------------------------------------------------------------

export default function NavEditor({
  ctx,
  savedLayout,
  onClose,
}: Readonly<{ ctx: NavVisibilityCtx; savedLayout: NavLayout | null; onClose: () => void }>) {
  const qc = useQueryClient();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pending = useRef<NavGroup[] | null>(null); // a debounced-but-unsaved layout
  const dragItem = useRef<{ gid: string; path: string } | null>(null);
  const dragGroup = useRef<string | null>(null);

  const [groups, setGroups] = useState<NavGroup[]>(() => gateFilter(resolveLayout(savedLayout), ctx));
  const [status, setStatus] = useState<SaveState>("idle");
  const [error, setError] = useState<string | null>(null);
  // Mirror the latest groups so `apply` can transform from current state without a
  // side-effecting state updater (keeps updates pure + StrictMode-safe).
  const groupsRef = useRef(groups);
  groupsRef.current = groups;

  // Persist a layout now and refresh the live sidebar. Stable so the debounce +
  // migration effects don't re-fire on every render.
  const persist = useCallback(
    async (layout: NavGroup[]) => {
      setStatus("saving");
      setError(null);
      try {
        await saveNavLayout(toNavLayout(layout));
        pending.current = null;
        setStatus("saved");
        qc.invalidateQueries({ queryKey: ["me"] });
      } catch (e) {
        setStatus("error");
        setError(e instanceof Error ? e.message : "Could not save navigation.");
      }
    },
    [qc],
  );

  const scheduleSave = useCallback(
    (layout: NavGroup[]) => {
      pending.current = layout;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => persist(layout), 500);
    },
    [persist],
  );

  // Apply a pure transform to the working copy + schedule its save.
  const apply = useCallback(
    (fn: (g: NavGroup[]) => NavGroup[]) => {
      const next = fn(groupsRef.current);
      setGroups(next);
      scheduleSave(next);
    },
    [scheduleSave],
  );

  // Open as a real modal (native <dialog>) so Esc + backdrop + focus are handled
  // the same way as the app's confirm/prompt dialogs. On close, FLUSH any pending
  // debounced change rather than dropping it (closing quickly must not lose an edit).
  useEffect(() => {
    const dlg = dialogRef.current;
    dlg?.showModal();
    return () => {
      clearTimeout(timer.current);
      const unsaved = pending.current;
      if (unsaved) {
        pending.current = null;
        saveNavLayout(toNavLayout(unsaved))
          .then(() => qc.invalidateQueries({ queryKey: ["me"] }))
          .catch(() => { /* editor is unmounting — nothing to surface */ });
      }
    };
  }, [qc]);

  // Legacy migration (run once): server layout is null but this device has legacy
  // per-device order/hidden prefs — persist the seeded layout so it becomes the
  // stored server layout. `groups` already carries the seeded state.
  const migrated = useRef(false);
  useEffect(() => {
    if (migrated.current) return;
    migrated.current = true;
    const hasLegacy = getNavOrder().length > 0 || getHiddenNavKeys().size > 0;
    if (savedLayout == null && hasLegacy) persist(groups);
  }, [savedLayout, groups, persist]);

  const resetToDefault = useCallback(async () => {
    clearTimeout(timer.current);
    pending.current = null; // drop any debounced edit — reset supersedes it
    setStatus("saving");
    setError(null);
    try {
      await resetNavLayout();
      setGroups(gateFilter(cloneGroups(DEFAULT_NAV_GROUPS), ctx));
      setStatus("saved");
      qc.invalidateQueries({ queryKey: ["me"] });
    } catch (e) {
      setStatus("error");
      setError(e instanceof Error ? e.message : "Could not reset navigation.");
    }
  }, [ctx, qc]);

  const actions: EditorActions = {
    ctx,
    groups,
    renameGroup: (id, label) => apply((g) => renameGroup(g, id, label)),
    setGroupIcon: (id, icon) => apply((g) => setGroupIcon(g, id, icon)),
    moveGroupBy: (index, delta) => apply((g) => moveGroup(g, index, index + delta)),
    deleteGroup: (id) => apply((g) => deleteGroup(g, id, ctx)),
    renameItem: (gid, path, label) => apply((g) => renameItem(g, gid, path, label)),
    toggleHidden: (gid, path) => apply((g) => toggleHiddenItem(g, gid, path)),
    moveItemBy: (gid, index, delta) => apply((g) => moveItemWithin(g, gid, index, index + delta)),
    moveItemTo: (fromGid, path, toGid) => apply((g) => moveItemToGroup(g, fromGid, path, toGid)),
    dragItem,
    dragGroup,
  };

  return (
    <dialog
      ref={dialogRef}
      className="modal-dialog nav-editor"
      aria-label="Customise navigation"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="card nav-editor__card">
        <div className="nav-editor__head">
          <h2 className="card__title" style={{ margin: 0 }}>Customise navigation</h2>
          <SaveIndicator status={status} />
          <button type="button" className="btn btn--ghost" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Rename, reorder, hide or regroup your sidebar. Changes save automatically and apply straight away.
        </p>
        {error && <p className="status status--error">{error}</p>}

        <div className="nav-editor__groups">
          {groups.map((group, index) => (
            <GroupBlock key={group.id} group={group} index={index} total={groups.length} actions={actions} />
          ))}
        </div>

        <AddGroupForm onAdd={(label, icon) => apply((g) => addCustomGroup(g, label, icon))} />

        <div className="nav-editor__foot">
          <button type="button" className="btn btn--ghost" onClick={resetToDefault}>
            Reset to default
          </button>
          <button type="button" className="btn" onClick={onClose}>Done</button>
        </div>
      </div>
    </dialog>
  );
}

function SaveIndicator({ status }: Readonly<{ status: SaveState }>) {
  const text: Record<SaveState, string> = {
    idle: "",
    saving: "Saving…",
    saved: "Saved ✓",
    error: "Save failed",
  };
  if (!text[status]) return null;
  const cls = status === "error" ? "status status--error" : "muted";
  return <span className={cls} role="status" aria-live="polite" style={{ fontSize: "0.82rem" }}>{text[status]}</span>;
}

// --- one group ----------------------------------------------------------------

function GroupBlock({
  group,
  index,
  total,
  actions,
}: Readonly<{ group: NavGroup; index: number; total: number; actions: EditorActions }>) {
  const custom = isCustomGroupId(group.id);
  const onDrop = () => {
    // A member dropped anywhere on this group → move it here (append). A group
    // dropped here → reorder groups so the dragged group lands at this index.
    const item = actions.dragItem.current;
    if (item) {
      actions.moveItemTo(item.gid, item.path, group.id);
      actions.dragItem.current = null;
      return;
    }
    const gid = actions.dragGroup.current;
    if (gid && gid !== group.id) {
      const from = actions.groups.findIndex((g) => g.id === gid);
      if (from !== -1) actions.moveGroupBy(from, index - from);
    }
    actions.dragGroup.current = null;
  };

  return (
    <section
      className="nav-editor__group"
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <div className="nav-editor__group-head">
        <button
          type="button"
          className="nav-editor__grip"
          aria-label={`Drag group ${groupTitle(group)}`}
          draggable
          onDragStart={() => { actions.dragGroup.current = group.id; }}
          onDragEnd={() => { actions.dragGroup.current = null; }}
        >
          ⠿
        </button>
        <input
          className="nav-editor__icon-input"
          aria-label={`Icon for ${groupTitle(group)}`}
          value={group.icon ?? ""}
          maxLength={4}
          placeholder="🙂"
          onChange={(e) => actions.setGroupIcon(group.id, e.target.value)}
        />
        <input
          className="nav-editor__group-name"
          aria-label={`Group name for ${groupTitle(group)}`}
          value={group.label ?? ""}
          placeholder={groupTitle(group)}
          onChange={(e) => actions.renameGroup(group.id, e.target.value)}
        />
        <span className="nav-editor__spacer" />
        <MoveButtons
          label={`group ${groupTitle(group)}`}
          canUp={index > 0}
          canDown={index < total - 1}
          onUp={() => actions.moveGroupBy(index, -1)}
          onDown={() => actions.moveGroupBy(index, +1)}
        />
        {custom && (
          <button
            type="button"
            className="btn btn--ghost nav-editor__del"
            aria-label={`Delete group ${groupTitle(group)}`}
            onClick={() => actions.deleteGroup(group.id)}
          >
            🗑
          </button>
        )}
      </div>
      <ul className="nav-editor__items">
        {group.items.map((it, i) => (
          <ItemLine
            key={it.path}
            gid={group.id}
            path={it.path}
            label={it.label}
            hidden={it.hidden === true}
            index={i}
            total={group.items.length}
            actions={actions}
          />
        ))}
        {group.items.length === 0 && <li className="muted nav-editor__empty">No pages — drag one here.</li>}
      </ul>
    </section>
  );
}

// --- one item -----------------------------------------------------------------

function ItemLine({
  gid,
  path,
  label,
  hidden,
  index,
  total,
  actions,
}: Readonly<{
  gid: string;
  path: string;
  label?: string;
  hidden: boolean;
  index: number;
  total: number;
  actions: EditorActions;
}>) {
  const others = actions.groups.filter((g) => g.id !== gid);
  const title = label ?? defaultLabelForPath(path);
  const onDrop = () => {
    const drag = actions.dragItem.current;
    actions.dragItem.current = null;
    if (!drag) return;
    if (drag.gid === gid) actions.moveItemBy(gid, findIndex(actions, gid, drag.path), index);
    else actions.moveItemTo(drag.gid, drag.path, gid);
  };

  return (
    <li
      className={"nav-editor__item" + (hidden ? " nav-editor__item--hidden" : "")}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <button
        type="button"
        className="nav-editor__grip"
        aria-label={`Drag ${title}`}
        draggable
        onDragStart={() => { actions.dragItem.current = { gid, path }; }}
        onDragEnd={() => { actions.dragItem.current = null; }}
      >
        ⠿
      </button>
      <span className="nav-editor__item-icon" aria-hidden="true">{defaultIconForPath(path)}</span>
      <input
        className="nav-editor__item-name"
        aria-label={`Label for ${defaultLabelForPath(path)}`}
        value={label ?? ""}
        placeholder={defaultLabelForPath(path)}
        onChange={(e) => actions.renameItem(gid, path, e.target.value)}
      />
      <span className="nav-editor__spacer" />
      <label className="nav-editor__toggle">
        <input
          type="checkbox"
          checked={!hidden}
          aria-label={`Show ${defaultLabelForPath(path)}`}
          onChange={() => actions.toggleHidden(gid, path)}
        />
        <span className="muted">{hidden ? "Hidden" : "Shown"}</span>
      </label>
      <MoveButtons
        label={title}
        canUp={index > 0}
        canDown={index < total - 1}
        onUp={() => actions.moveItemBy(gid, index, -1)}
        onDown={() => actions.moveItemBy(gid, index, +1)}
      />
      {others.length > 0 && (
        <label className="nav-editor__moveto">
          <select
            aria-label={`Move ${title} to group`}
            value=""
            onChange={(e) => { if (e.target.value) actions.moveItemTo(gid, path, e.target.value); }}
          >
            <option value="">Move to…</option>
            {others.map((g) => (
              <option key={g.id} value={g.id}>{groupTitle(g)}</option>
            ))}
          </select>
        </label>
      )}
    </li>
  );
}

function findIndex(actions: EditorActions, gid: string, path: string): number {
  return actions.groups.find((g) => g.id === gid)?.items.findIndex((it) => it.path === path) ?? -1;
}

// Shared ▲▼ move controls (the touch-friendly fallback to HTML5 drag).
function MoveButtons({
  label,
  canUp,
  canDown,
  onUp,
  onDown,
}: Readonly<{ label: string; canUp: boolean; canDown: boolean; onUp: () => void; onDown: () => void }>) {
  return (
    <span className="nav-editor__moves">
      <button type="button" className="nav-editor__move" aria-label={`Move ${label} up`} disabled={!canUp} onClick={onUp}>▲</button>
      <button type="button" className="nav-editor__move" aria-label={`Move ${label} down`} disabled={!canDown} onClick={onDown}>▼</button>
    </span>
  );
}

// --- create a custom group ----------------------------------------------------

function AddGroupForm({ onAdd }: Readonly<{ onAdd: (label: string, icon: string) => void }>) {
  const [label, setLabel] = useState("");
  const [icon, setIcon] = useState("");
  const submit = () => {
    const name = label.trim();
    if (!name) return;
    onAdd(name, icon.trim());
    setLabel("");
    setIcon("");
  };
  return (
    <form
      className="nav-editor__add"
      onSubmit={(e) => { e.preventDefault(); submit(); }}
    >
      <input
        className="nav-editor__icon-input"
        aria-label="New group icon"
        value={icon}
        maxLength={4}
        placeholder="🙂"
        onChange={(e) => setIcon(e.target.value)}
      />
      <input
        aria-label="New group name"
        value={label}
        placeholder="New group name"
        onChange={(e) => setLabel(e.target.value)}
      />
      <button type="submit" className="btn" disabled={!label.trim()}>Add group</button>
    </form>
  );
}
