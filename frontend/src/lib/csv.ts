import type { BatchSuggestion, CloudBatchItem } from "../api/client";

// Escape one CSV cell: quote if it holds a comma/quote/newline and double any
// internal quotes (RFC-4180). Shared by the local + cloud AI batch panels.
export function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

// Serialise AI suggestions to CSV (description, suggested category, confidence).
export function suggestionsToCsv(rows: BatchSuggestion[]): string {
  const header = ["Description", "Suggested category", "Confidence"].join(",");
  const lines = rows.map((s) =>
    [csvCell(s.description), csvCell(s.category_name), s.confidence == null ? "" : String(s.confidence)].join(","),
  );
  return [header, ...lines].join("\r\n");
}

// Serialise the redacted "will send" cloud review list to CSV — only the
// description/amount/currency already shown in the UI, nothing more sensitive.
export function cloudItemsToCsv(rows: CloudBatchItem[]): string {
  const header = ["Description", "Amount", "Currency"].join(",");
  const lines = rows.map((it) => [csvCell(it.description), csvCell(it.amount), csvCell(it.currency)].join(","));
  return [header, ...lines].join("\r\n");
}

// Client-side blob download — no lib CSV helper exists (the api/client one fetches
// a server endpoint), so a small inline anchor click does the job.
export function downloadCsvFile(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
