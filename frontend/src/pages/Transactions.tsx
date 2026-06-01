import { Fragment, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  categoriseTransaction,
  classifyWithAi,
  getAiStatus,
  getSettings,
  listCategories,
  listProjects,
  listTransactions,
  recategorise,
  setTransactionTags,
  updateTransaction,
  type Transaction,
  type TransactionFilters,
} from "../api/client";
import SplitEditor from "../components/SplitEditor";
import AiBatchPanel from "../components/AiBatchPanel";

const PAGE_SIZE = 50;

export default function Transactions() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [needsReview, setNeedsReview] = useState(false);
  const [page, setPage] = useState(0);
  const [splitId, setSplitId] = useState<number | null>(null);
  const [showAiBatch, setShowAiBatch] = useState(false);

  const filters: TransactionFilters = {
    search: search || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    needs_review: needsReview || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };

  const categories = useQuery({ queryKey: ["categories"], queryFn: listCategories });
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const aiStatus = useQuery({ queryKey: ["ai-status"], queryFn: getAiStatus });
  const base = settings.data?.base_currency ?? "GBP";
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["transactions", filters],
    queryFn: () => listTransactions(filters),
    placeholderData: keepPreviousData,
  });

  const setCategory = useMutation({
    mutationFn: (v: { id: number; categoryId: number | null }) =>
      categoriseTransaction(v.id, v.categoryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  // "Make rule": keep this category AND create a rule so similar future
  // transactions auto-categorise (spec §15.3).
  const makeRule = useMutation({
    mutationFn: (v: { id: number; categoryId: number }) =>
      categoriseTransaction(v.id, v.categoryId, { learnRule: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["rules"] });
    },
  });

  const recat = useMutation({
    mutationFn: () => recategorise(true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transactions"] }),
  });

  const setProject = useMutation({
    mutationFn: (v: { id: number; projectId: number | null }) =>
      updateTransaction(v.id, { project_id: v.projectId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard-projects"] });
    },
  });

  const setTags = useMutation({
    mutationFn: (v: { id: number; tags: string[] }) => setTransactionTags(v.id, v.tags),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["tags"] });
    },
  });

  // Add a tag via a small prompt, then persist the new full set (spec §18.3).
  function addTag(t: Transaction) {
    const name = window.prompt("Add a tag (e.g. reimbursable, work, gift):")?.trim();
    if (!name) return;
    const current = (t.tags ?? []).map((x) => x.name);
    if (current.some((c) => c.toLowerCase() === name.toLowerCase())) return;
    setTags.mutate({ id: t.id, tags: [...current, name] });
  }
  function removeTag(t: Transaction, name: string) {
    setTags.mutate({ id: t.id, tags: (t.tags ?? []).map((x) => x.name).filter((n) => n !== name) });
  }

  // AI suggests a category (never auto-applies). Cloud-manual mode previews the
  // redacted payload and asks for approval first (spec §22.5).
  async function suggestAi(t: Transaction) {
    try {
      let res = await classifyWithAi(t.id);
      if (res.status === "approval_required") {
        const preview = JSON.stringify(res.payload ?? {}, null, 2);
        if (!window.confirm(`Cloud AI needs approval. Only this redacted payload is sent:\n\n${preview}\n\nApprove?`)) return;
        res = await classifyWithAi(t.id, true);
      }
      if (res.status === "ok" && res.category_id) {
        const pct = res.confidence != null ? ` (${Math.round(res.confidence * 100)}%)` : "";
        if (window.confirm(`AI suggests: ${res.category_name}${pct}\n${res.rationale ?? ""}\n\nApply this category?`)) {
          setCategory.mutate({ id: t.id, categoryId: res.category_id });
        }
      } else {
        window.alert("AI couldn't suggest a category for this transaction.");
      }
    } catch (e) {
      window.alert(String(e instanceof Error ? e.message : e));
    }
  }

  const total = data?.total ?? 0;
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Transactions</h1>
        <div style={{ display: "flex", gap: 8 }}>
          {aiStatus.data?.enabled && aiStatus.data?.privacy_mode === "local_llm" && (
            <button className="btn btn--ghost" onClick={() => setShowAiBatch((v) => !v)}>
              {showAiBatch ? "Hide AI categorise" : "✨ AI categorise…"}
            </button>
          )}
          <button className="btn btn--ghost" disabled={recat.isPending} onClick={() => recat.mutate()}>
            {recat.isPending ? "Re-categorising…" : "Re-categorise uncategorised"}
          </button>
        </div>
      </div>
      {recat.isSuccess && (
        <p className="muted">Re-categorised {recat.data.recategorised} transaction(s).</p>
      )}

      {showAiBatch && <AiBatchPanel base={base} onClose={() => setShowAiBatch(false)} />}

      <div className="card">
        <div className="filters">
          <input
            placeholder="Search description / merchant"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(0); }}
          />
          <label>From <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(0); }} /></label>
          <label>To <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(0); }} /></label>
          <label className="checkbox">
            <input type="checkbox" checked={needsReview} onChange={(e) => { setNeedsReview(e.target.checked); setPage(0); }} />
            Needs review
          </label>
        </div>
      </div>

      <div className="card">
        {isLoading && <p className="muted">Loading…</p>}
        {isError && <p className="status status--error">{String(error)}</p>}
        {data && data.items.length === 0 && (
          <p className="muted">
            No transactions. Import a CSV on the <strong>Import</strong> page to get started.
          </p>
        )}
        {data && data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Description</th>
                    <th className="num">Amount</th>
                    <th>Category</th>
                    <th>Project</th>
                    <th>Flags &amp; tags</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((t) => (
                    <Fragment key={t.id}>
                    <tr>
                      <td>{t.transaction_date}</td>
                      <td>
                        {t.description_raw}
                        {t.merchant_raw && t.merchant_raw !== t.description_raw && (
                          <span className="muted"> · {t.merchant_raw}</span>
                        )}
                      </td>
                      <td className={"num " + (t.direction === "credit" ? "amt--pos" : "amt--neg")}>
                        {t.amount}
                        {t.currency !== base && <span className="muted"> {t.currency}</span>}
                        {t.currency !== base && t.needs_rate && (
                          <span className="tag tag--dup">rate?</span>
                        )}
                        {t.currency !== base && !t.needs_rate && t.base_amount && (
                          <div className="muted" style={{ fontSize: "0.78rem" }}>
                            ≈ {t.base_amount} {base}
                          </div>
                        )}
                      </td>
                      <td>
                        {t.is_split ? (
                          <span className="muted">Split across categories</span>
                        ) : (
                          <>
                            <select
                              className={t.category_id ? "" : "select--empty"}
                              value={t.category_id ?? ""}
                              onChange={(e) =>
                                setCategory.mutate({
                                  id: t.id,
                                  categoryId: e.target.value ? Number(e.target.value) : null,
                                })
                              }
                            >
                              <option value="">— uncategorised —</option>
                              {categories.data?.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.name}
                                </option>
                              ))}
                            </select>
                            {t.category_id !== null && (
                              <button
                                className="link-btn"
                                title="Create a rule so similar transactions auto-categorise"
                                style={{ marginLeft: 6 }}
                                onClick={() => makeRule.mutate({ id: t.id, categoryId: t.category_id! })}
                              >
                                + rule
                              </button>
                            )}
                            {t.category_id === null && aiStatus.data?.enabled && (
                              <button
                                className="link-btn"
                                title="Ask the AI assistant to suggest a category"
                                style={{ marginLeft: 6 }}
                                onClick={() => suggestAi(t)}
                              >
                                ✨ suggest
                              </button>
                            )}
                          </>
                        )}
                      </td>
                      <td>
                        <select
                          className={t.project_id ? "" : "select--empty"}
                          value={t.project_id ?? ""}
                          onChange={(e) =>
                            setProject.mutate({
                              id: t.id,
                              projectId: e.target.value ? Number(e.target.value) : null,
                            })
                          }
                        >
                          <option value="">— none —</option>
                          {projects.data?.map((p) => (
                            <option key={p.id} value={p.id}>{p.name}</option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {t.is_split && <span className="tag">split</span>}
                        {t.is_transfer && <span className="tag">transfer</span>}
                        {t.is_income && <span className="tag">income</span>}
                        {t.needs_review && <span className="tag tag--dup">review</span>}
                        {(t.tags ?? []).map((tag) => (
                          <span
                            key={tag.id}
                            className="tag"
                            title="Click to remove"
                            style={{ cursor: "pointer", background: tag.colour ?? undefined }}
                            onClick={() => removeTag(t, tag.name)}
                          >
                            {tag.name} ✕
                          </span>
                        ))}
                        <button className="link-btn" style={{ marginLeft: 6 }} onClick={() => addTag(t)}>
                          + tag
                        </button>
                        <button
                          className="link-btn"
                          style={{ marginLeft: 6 }}
                          onClick={() => setSplitId(splitId === t.id ? null : t.id)}
                        >
                          {t.is_split ? "edit split" : "split"}
                        </button>
                      </td>
                    </tr>
                    {splitId === t.id && (
                      <tr>
                        <td colSpan={6} style={{ background: "rgba(127,127,127,0.06)" }}>
                          <SplitEditor
                            txnId={t.id}
                            amount={t.amount}
                            currency={t.currency}
                            isSplit={t.is_split}
                            categories={categories.data ?? []}
                            onDone={() => setSplitId(null)}
                          />
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button className="btn btn--ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                ← Prev
              </button>
              <span className="muted">{total} total · page {page + 1} of {maxPage + 1}</span>
              <button className="btn btn--ghost" disabled={page >= maxPage} onClick={() => setPage((p) => p + 1)}>
                Next →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
