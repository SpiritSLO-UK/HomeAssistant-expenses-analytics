// Shared "how far back" range picker for the over-time charts (Travel, Projects,
// Savings, Investments). Emits a month count; the chart endpoints take `months`.
// "All" has no real month limit; callers pass `months` straight to the history
// endpoints (or via monthsToDays), so a large sentinel — 1200 months = 100 years
// — reaches back past any real data without changing the existing contract.
const ALL_MONTHS = 1200;
const RANGES: { months: number; label: string }[] = [
  { months: 6, label: "6M" },
  { months: 12, label: "1Y" },
  { months: 24, label: "2Y" },
  { months: 60, label: "5Y" },
  { months: ALL_MONTHS, label: "All" },
];

export default function RangeSelector({
  months,
  onChange,
}: Readonly<{ months: number; onChange: (months: number) => void }>) {
  return (
    <div className="form-row" style={{ gap: 4 }} title="Time range">
      {RANGES.map((r) => (
        <button
          key={r.months}
          type="button"
          aria-pressed={months === r.months}
          className={"btn btn--sm" + (months === r.months ? "" : " btn--ghost")}
          onClick={() => onChange(r.months)}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}
