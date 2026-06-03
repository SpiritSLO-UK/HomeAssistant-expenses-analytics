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

  const invalidate = () => {
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
  });

  const toggle = useMutation({
    mutationFn: (v: { id: number; enabled: boolean }) => updateRule(v.id, { enabled: v.enabled }),
    onSuccess: invalidate,
  });
  const setPrio = useMutation({
    mutationFn: (v: { id: number; priority: number }) => updateRule(v.id, { priority: v.priority }),
    onSuccess: invalidate,
  });
  const remove = useMutation({ mutationFn: (id: number) => deleteRule(id), onSuccess: invalidate });
  const runTest = useMutation({
    mutationFn: () => testRule(conditionType, conditionValue),
    onSuccess: setTest,
  });

  const catName = (id: string | null) =>
    id ? categories.data?.find((c) => String(c.id) === id)?.name ?? id : "";

  function describeAction(r: Rule): string {
    if (r.action_type === "set_category") return `→ category: ${catName(r.action_value)}`;
    if (r.action_type === "set_vendor") {
      const v = vendors.data?.find((x) => String(x.id) === r.action_value);
      return `→ vendor: ${v?.canonical_name ?? r.action_value}`;
    }
    return `→ ${r.action_type.replace("_", " ")}${r.action_value ? `: ${r.action_value}` : ""}`;
  }

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Rules</h1>
        <button className="btn btn--ghost" onClick={() => setHelp((v) => !v)}>
          {help ? "Hide help" : "❔ How rules work"}
        </button>
      </div>

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
          {!NO_VALUE_ACTIONS.has(actionType) && actionType !== "set_category" && actionType !== "set_vendor" && (
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
                <tr><th>On</th><th>Name</th><th>Condition</th><th>Action</th><th className="num">Prio</th><th></th></tr>
              </thead>
              <tbody>
                {rules.data.map((r) => (
                  <tr key={r.id} style={{ opacity: r.enabled ? 1 : 0.5 }}>
                    <td>
                      <input type="checkbox" checked={r.enabled} onChange={(e) => toggle.mutate({ id: r.id, enabled: e.target.checked })} />
                    </td>
                    <td>{r.name}{r.created_from === "manual_correction" && <span className="tag"> learned</span>}</td>
                    <td className="muted">{r.condition_type.replace(/_/g, " ")}: “{r.condition_value}”</td>
                    <td>{describeAction(r)}</td>
                    <td className="num">
                      <input type="number" style={{ width: 60 }} defaultValue={r.priority}
                        onBlur={(e) => { const p = Number(e.target.value); if (p !== r.priority) setPrio.mutate({ id: r.id, priority: p }); }} />
                    </td>
                    <td><button className="btn btn--ghost" onClick={() => remove.mutate(r.id)}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
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
        manual choices</strong>: manual → <strong>rule</strong> → vendor default → keyword. Among rules,
        <strong> higher priority wins</strong> (your rules default to 150–200; the built-in library uses ~100).
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
      </ol>
      <p className="muted" style={{ marginBottom: 0 }}>
        Tip: the quickest way to make a rule is “make rule” on a transaction you've just corrected — it
        pre-fills the condition for you.
      </p>
    </div>
  );
}
