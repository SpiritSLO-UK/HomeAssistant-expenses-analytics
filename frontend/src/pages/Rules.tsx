import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RULE_ACTION_TYPES,
  RULE_CONDITION_TYPES,
  createRule,
  deleteRule,
  listCategories,
  listRules,
  listVendors,
  testRule,
  updateRule,
  type Rule,
  type RuleTestResult,
} from "../api/client";
import { useServerState } from "../lib/useServerState";
import { useConfirm } from "../components/dialogs";

const NO_VALUE_ACTIONS = new Set([
  "mark_transfer",
  "mark_income",
  "mark_subscription",
  "require_review",
  "block_cloud_ai",
]);

export default function Rules() {
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["rules"], queryFn: listRules });
  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const vendors = useQuery({ queryKey: ["vendors"], queryFn: listVendors });

  const [conditionType, setConditionType] = useState<string>("description_contains");
  const [conditionValue, setConditionValue] = useState("");
  const [actionType, setActionType] = useState<string>("set_category");
  const [actionValue, setActionValue] = useState("");
  const [priority, setPriority] = useState(150);
  const [test, setTest] = useState<RuleTestResult | null>(null);
  const [help, setHelp] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const [err, setErr] = useState<string | null>(null);
  const fail = (e: unknown) => setErr(String(e));
  // Cleared on success too, so a stale banner doesn't outlive a later success (FE-3).
  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["rules"] });
    qc.invalidateQueries({ queryKey: ["transactions"] });
  };

  const create = useMutation({
    mutationFn: () =>
      createRule({
        condition_type: conditionType,
        condition_value: conditionValue,
        action_type: actionType,
        action_value: NO_VALUE_ACTIONS.has(actionType) ? null : actionValue || null,
        priority,
      }),
    onSuccess: () => {
      setConditionValue("");
      setActionValue("");
      setTest(null);
      invalidate();
    },
    onError: fail,
  });

  const toggle = useMutation({
    mutationFn: (v: { id: number; enabled: boolean }) => updateRule(v.id, { enabled: v.enabled }),
    onSuccess: invalidate,
    onError: fail,
  });
  const setPrio = useMutation({
    mutationFn: (v: { id: number; priority: number }) => updateRule(v.id, { priority: v.priority }),
    onSuccess: invalidate,
    onError: fail,
  });
  const remove = useMutation({ mutationFn: (id: number) => deleteRule(id), onSuccess: invalidate, onError: fail });
  // Clone reuses the create-rule API: same condition/action, name suffixed and
  // priority nudged up by 1 so the copy lands just above its original.
  const clone = useMutation({
    mutationFn: (r: Rule) =>
      createRule({
        condition_type: r.condition_type,
        condition_value: r.condition_value,
        action_type: r.action_type,
        action_value: r.action_value,
        name: `${r.name} (copy)`,
        priority: r.priority + 1,
        enabled: r.enabled,
      }),
    onSuccess: invalidate,
    onError: fail,
  });
  const runTest = useMutation({
    mutationFn: () => testRule(conditionType, conditionValue),
    onSuccess: (r) => {
      setErr(null);
      setTest(r);
    },
    onError: fail,
  });

  const catName = (id: string | null) =>
    id ? categories.data?.find((c) => String(c.id) === id)?.name ?? id : "";

  function describeAction(r: Rule): string {
    if (r.action_type === "set_category") return `→ category: ${catName(r.action_value)}`;
    if (r.action_type === "set_vendor") {
      const v = vendors.data?.find((x) => String(x.id) === r.action_value);
      return `→ vendor: ${v?.canonical_name ?? r.action_value}`;
    }
    if (r.action_type === "set_country") return `→ country: ${r.action_value}`;
    return `→ ${r.action_type.replace(/_/g, " ")}` + (r.action_value ? `: ${r.action_value}` : "");
  }

  // Drop the dragged row onto `targetIndex`, then persist only the rules whose
  // priority actually changed through the existing priority-update mutation.
  function handleDrop(targetIndex: number) {
    const data = rules.data;
    if (data == null || dragIndex == null) return;
    const changes = priorityUpdates(data, dragIndex, targetIndex);
    setDragIndex(null);
    changes.forEach((c) => setPrio.mutate(c));
  }

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Rules</h1>
        <button className="btn btn--ghost" onClick={() => setHelp((v) => !v)}>
          {help ? "Hide help" : "❔ How rules work"}
        </button>
      </div>
      {err && <p className="status status--error">{err}</p>}

      {help && <RulesHelp />}

      <div className="card">
        <h2 className="card__title">New rule</h2>
        <div className="form-row">
          <span className="muted">If</span>
          <select value={conditionType} onChange={(e) => setConditionType(e.target.value)}>
            {RULE_CONDITION_TYPES.map((c) => (
              <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
            ))}
          </select>
          <input
            placeholder={conditionType === "amount_between" ? "lo,hi (e.g. -100,-10)" : "value"}
            value={conditionValue}
            onChange={(e) => setConditionValue(e.target.value)}
          />
          <span className="muted">then</span>
          <select value={actionType} onChange={(e) => { setActionType(e.target.value); setActionValue(""); }}>
            {RULE_ACTION_TYPES.map((a) => (
              <option key={a} value={a}>{a.replace(/_/g, " ")}</option>
            ))}
          </select>
          {actionType === "set_category" && (
            <select value={actionValue} onChange={(e) => setActionValue(e.target.value)}>
              <option value="">choose category…</option>
              {categories.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          {actionType === "set_vendor" && (
            <select value={actionValue} onChange={(e) => setActionValue(e.target.value)}>
              <option value="">choose vendor…</option>
              {vendors.data?.map((v) => <option key={v.id} value={v.id}>{v.canonical_name}</option>)}
            </select>
          )}
          {actionType === "set_country" && (
            <input
              style={{ width: 90 }}
              placeholder="ES, GB, US…"
              value={actionValue}
              onChange={(e) => setActionValue(e.target.value.toUpperCase().slice(0, 2))}
            />
          )}
          {!NO_VALUE_ACTIONS.has(actionType) &&
            actionType !== "set_category" &&
            actionType !== "set_vendor" &&
            actionType !== "set_country" && (
              <input placeholder="value" value={actionValue} onChange={(e) => setActionValue(e.target.value)} />
            )}
          <label className="muted">prio <input type="number" style={{ width: 70 }} value={priority} onChange={(e) => setPriority(Number(e.target.value))} /></label>
        </div>
        <div className="form-row" style={{ marginTop: 8 }}>
          <button className="btn btn--ghost" disabled={!conditionValue || runTest.isPending} onClick={() => runTest.mutate()}>
            Test
          </button>
          <button
            className="btn"
            disabled={!conditionValue || (!NO_VALUE_ACTIONS.has(actionType) && !actionValue) || create.isPending}
            onClick={() => create.mutate()}
          >
            Create rule
          </button>
          {test && (
            <span className="muted">
              Matches {test.match_count} of {test.total} transactions
              {test.sample[0] ? ` — e.g. “${test.sample[0].description_raw}”` : ""}
            </span>
          )}
        </div>
        <p className="muted">
          Rules run on import and re-categorise, just below your manual choices (manual → rule →
          vendor → keyword). Higher priority wins (your rules default to 150–200; the library uses ~100).
        </p>
      </div>

      <div className="card">
        <h2 className="card__title">Rules ({rules.data?.length ?? 0})</h2>
        {rules.data?.length === 0 && (
          <p className="muted">No rules yet. Create one above, or use “make rule” on a transaction.</p>
        )}
        {rules.data && rules.data.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th></th><th>On</th><th>Name</th><th>Condition</th><th>Action</th><th className="num">Prio</th><th></th></tr>
              </thead>
              <tbody>
                {rules.data.map((r, i) => (
                  <RuleRow
                    key={r.id}
                    rule={r}
                    dragging={dragIndex === i}
                    describeAction={describeAction}
                    onDragStart={() => setDragIndex(i)}
                    onDragEnd={() => setDragIndex(null)}
                    onDropRow={() => handleDrop(i)}
                    onToggle={(enabled) => toggle.mutate({ id: r.id, enabled })}
                    onCommitPriority={(priority) => setPrio.mutate({ id: r.id, priority })}
                    onClone={() => clone.mutate(r)}
                    onDelete={() => remove.mutate(r.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// Pure: move `from` to `to` in a copy of the list.
function reorder<T>(list: readonly T[], from: number, to: number): T[] {
  const next = list.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Pure: recompute priorities after a drag. The pool of existing priority values
// (sorted high→low) is reassigned to the new visual order, so numbers stay in
// the same range and only the rules that actually moved are returned to persist.
function priorityUpdates(rules: readonly Rule[], from: number, to: number): { id: number; priority: number }[] {
  if (from === to) return [];
  const ordered = reorder(rules, from, to);
  const pool = rules.map((r) => r.priority).sort((a, b) => b - a);
  const changes: { id: number; priority: number }[] = [];
  ordered.forEach((r, i) => {
    if (r.priority !== pool[i]) changes.push({ id: r.id, priority: pool[i] });
  });
  return changes;
}

interface RuleRowProps {
  rule: Rule;
  dragging: boolean;
  describeAction: (r: Rule) => string;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDropRow: () => void;
  onToggle: (enabled: boolean) => void;
  onCommitPriority: (priority: number) => void;
  onClone: () => void;
  onDelete: () => void;
}

function RuleRow(props: RuleRowProps) {
  const { rule, dragging, describeAction } = props;
  const confirm = useConfirm();
  let opacity = 1;
  if (!rule.enabled) opacity = 0.5;
  if (dragging) opacity = 0.4;
  return (
    <tr
      draggable
      onDragStart={props.onDragStart}
      onDragEnd={props.onDragEnd}
      onDragOver={(e) => e.preventDefault()}
      onDrop={props.onDropRow}
      style={{ opacity }}
    >
      <td
        aria-hidden="true"
        title="Drag to reorder priority"
        style={{ cursor: "grab", color: "var(--muted, #888)", userSelect: "none" }}
      >
        ⠿
      </td>
      <td>
        <input
          type="checkbox"
          checked={rule.enabled}
          onChange={(e) => props.onToggle(e.target.checked)}
        />
      </td>
      <td>{rule.name}{rule.created_from === "manual_correction" && <span className="tag"> learned</span>}</td>
      <td className="muted">{rule.condition_type.replace(/_/g, " ")}: “{rule.condition_value}”</td>
      <td>{describeAction(rule)}</td>
      <PriorityCell rule={rule} onCommit={props.onCommitPriority} />
      <td>
        <button className="btn btn--ghost" onClick={props.onClone}>Clone</button>
        <button
          className="btn btn--ghost"
          onClick={async () => {
            if (await confirm({ message: `Delete the rule “${rule.name}”? This can't be undone (existing transactions keep their current categories).`, confirmLabel: "Delete", danger: true }))
              props.onDelete();
          }}
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

function PriorityCell({ rule, onCommit }: { rule: Rule; onCommit: (priority: number) => void }) {
  // Controlled + re-syncs to the server value after a refetch, so edits aren't
  // lost on navigation and the field never goes stale (FE-7).
  const [priority, setPriority] = useServerState(rule.priority);
  return (
    <td className="num">
      <input
        type="number"
        style={{ width: 60 }}
        value={priority}
        onChange={(e) => setPriority(Number(e.target.value))}
        onBlur={() => { if (priority !== rule.priority) onCommit(priority); }}
      />
    </td>
  );
}

const CONDITION_HELP: { type: string; what: string; example: string }[] = [
  { type: "description_contains", what: "text anywhere in the raw description (case-insensitive)", example: "NETFLIX" },
  { type: "merchant_contains", what: "text in the cleaned-up merchant name", example: "TFL" },
  { type: "vendor_equals", what: "a specific vendor you've set up", example: "(pick a vendor)" },
  { type: "account_equals", what: "transactions on one account", example: "(an account id)" },
  { type: "category_equals", what: "transactions already in a category — handy to re-route", example: "(a category id)" },
  { type: "amount_equals", what: "an exact amount (spend is negative)", example: "-9.99" },
  { type: "amount_between", what: "a low,high range (signed)", example: "-100,-10" },
];

const ACTION_HELP: { type: string; what: string }[] = [
  { type: "set_category", what: "put the transaction in a category" },
  { type: "set_vendor", what: "tag it with a vendor" },
  { type: "set_project", what: "assign it to a project" },
  { type: "set_country", what: "tag its spend location (ISO alpha-2, e.g. ES) for the location map" },
  { type: "mark_transfer", what: "flag it as a transfer (excluded from spend)" },
  { type: "mark_income", what: "flag it as income" },
  { type: "mark_subscription", what: "flag it as a recurring subscription" },
  { type: "require_review", what: "send it to the Review Queue to check" },
  { type: "block_cloud_ai", what: "never send this transaction to a cloud AI" },
];

function RulesHelp() {
  return (
    <div className="card" style={{ borderLeft: "3px solid var(--sidebar-active, #3b82f6)" }}>
      <h2 className="card__title">How rules work</h2>
      <p className="muted">
        Rules apply automatically on import and when you re-categorise. They run <strong>just below your
        manual choices</strong>: manual → <strong>rule</strong> → vendor default → keyword. Among rules,{" "}
        <strong>higher priority wins</strong> (your rules default to 150–200; the built-in library uses ~100).
        Toggle any rule off without deleting it, and use <strong>Test</strong> to see how many existing
        transactions a condition matches before you create it.
      </p>

      <h3 style={{ marginBottom: 4 }}>Conditions — the “if”</h3>
      <ul className="kv">
        {CONDITION_HELP.map((c) => (
          <li key={c.type}>
            <span><code>{c.type.replace(/_/g, " ")}</code> — {c.what}</span>
            <span className="muted">e.g. {c.example}</span>
          </li>
        ))}
      </ul>

      <h3 style={{ marginBottom: 4, marginTop: 12 }}>Actions — the “then”</h3>
      <ul className="kv">
        {ACTION_HELP.map((a) => (
          <li key={a.type}>
            <span><code>{a.type.replace(/_/g, " ")}</code></span>
            <span className="muted">{a.what}</span>
          </li>
        ))}
      </ul>

      <h3 style={{ marginBottom: 4, marginTop: 12 }}>Worked examples</h3>
      <ol className="muted" style={{ marginTop: 0, paddingLeft: 18 }}>
        <li><strong>Coffee → Eating Out:</strong> if <em>description contains</em> “COSTA”, then <em>set category</em> Eating Out.</li>
        <li><strong>Catch big one-offs:</strong> if <em>amount between</em> “-100000,-500”, then <em>require review</em>.</li>
        <li><strong>Keep payslips private:</strong> if <em>description contains</em> “SALARY”, then <em>block cloud AI</em>.</li>
        <li><strong>Commute as transport:</strong> if <em>merchant contains</em> “TFL”, then <em>set category</em> Transport (priority 200 so it beats the library).</li>
        <li><strong>Map a foreign vendor:</strong> if <em>description contains</em> “MERCADONA”, then <em>set country</em> ES — so the spend-by-location map credits Spain, not the EUR currency fallback.</li>
      </ol>
      <p className="muted" style={{ marginBottom: 0 }}>
        Tip: the quickest way to make a rule is “make rule” on a transaction you've just corrected — it
        pre-fills the condition for you.
      </p>
    </div>
  );
}
