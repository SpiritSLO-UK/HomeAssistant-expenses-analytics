// Shared "how far back" range picker for the over-time charts (Travel, Projects,
// Savings, Investments). Emits a month count; the chart endpoints take `months`.
const RANGES: { months: number; label: string }[] = [
  { months: 6, label: "6M" },
  { months: 12, label: "1Y" },
  { months: 24, label: "2Y" },
  { months: 60, label: "5Y" },
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
