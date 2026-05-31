import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listReviewItems, setReviewStatus, type ReviewItem } from "../api/client";

const REASON_LABEL: Record<string, string> = {
  unknown_vendor: "Unknown vendor",
  unknown_category: "Uncategorised",
  low_confidence: "Low confidence",
  duplicate_possible: "Possible duplicate",
  receipt_unmatched: "Unmatched receipt",
  split_invalid: "Invalid split",
  cloud_ai_approval_required: "Cloud AI approval",
  sensitive_data_detected: "Sensitive data",
  parser_error: "Parser error",
};

const SEVERITY_COLOUR: Record<string, string> = {
  info: "rgba(127,127,127,0.25)",
  warning: "#d8930a",
  error: "#c0392b",
};

export default function ReviewQueue() {
  const qc = useQueryClient();
  const [showResolved, setShowResolved] = useState(false);
  const status = showResolved ? "resolved" : "open";

  const items = useQuery({ queryKey: ["review", status], queryFn: () => listReviewItems(status) });

  const update = useMutation({
    mutationFn: (v: { id: number; status: string }) => setReviewStatus(v.id, v.status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["review"] }),
  });

  return (
    <div className="page">
      <div className="page__head">
        <h1 className="page__title">Review Queue</h1>
        <label className="checkbox">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
          Show resolved
        </label>
      </div>
      <p className="muted">
        Things the app wasn't sure about — unknown vendors, low-confidence reads, unmatched receipts.
        Resolve or ignore each one.
      </p>

      <div className="card">
        {items.isLoading && <p className="muted">Loading…</p>}
        {items.data && items.data.length === 0 && (
          <p className="muted">{showResolved ? "Nothing resolved yet." : "All clear — nothing to review. 🎉"}</p>
        )}
        <div>
          {items.data?.map((item: ReviewItem) => (
            <div key={item.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "10px 0", borderBottom: "1px solid rgba(127,127,127,0.2)" }}>
              <div>
                <span className="tag" style={{ background: SEVERITY_COLOUR[item.severity] }}>
                  {REASON_LABEL[item.reason] ?? item.reason}
                </span>{" "}
                <span className="muted">{item.item_type}{item.item_id != null ? ` #${item.item_id}` : ""}</span>
                {item.suggested_action && <div style={{ marginTop: 4 }}>{item.suggested_action}</div>}
              </div>
              {!showResolved && (
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button className="btn btn--ghost" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, status: "resolved" })}>Resolve</button>
                  <button className="link-btn" disabled={update.isPending} onClick={() => update.mutate({ id: item.id, status: "ignored" })}>Ignore</button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
