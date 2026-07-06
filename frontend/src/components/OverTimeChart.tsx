import Sparkline from "./Sparkline";
import RangeSelector from "./RangeSelector";
import type { TimeSeries } from "../api/client";

// A page-level "X over time" card: a title, a range picker, a sparkline of the
// monthly totals, and a caption. Shared by Travel / Projects (and Savings /
// Investments), so the period-over-time views look and behave the same.
export default function OverTimeChart({
  title,
  series,
  months,
  onMonths,
  color = "#6aa9ff",
  emptyHint = "Not enough history yet — it builds up as transactions accumulate.",
}: Readonly<{
  title: string;
  series: TimeSeries | undefined;
  months: number;
  onMonths: (months: number) => void;
  color?: string;
  emptyHint?: string;
}>) {
  const points = series?.months ?? [];
  const values = points.map((m) => Number(m.total));
  const cur = series?.currency ?? "GBP";
  const total = values.reduce((a, b) => a + b, 0);
  // Two or more months IS history — a genuinely all-zero stretch is real data
  // (now drawn as a centred flat line), not "not enough history".
  const hasData = values.length >= 2;
  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="card__title" style={{ margin: 0 }}>{title}</h2>
        <RangeSelector months={months} onChange={onMonths} />
      </div>
      {hasData ? (
        <>
          {/* 560x120 is the viewBox coordinate space; Sparkline renders at
              width:100% (capped at 560) so the chart scales within the card
              instead of forcing a fixed pixel width / widening the page. */}
          <Sparkline values={values} color={color} width={560} height={120} label={`${title} over time`} />
          <p className="muted" style={{ marginBottom: 0 }}>
            {points[0].month} – {points[points.length - 1].month} · total{" "}
            {cur} {total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </p>
        </>
      ) : (
        <p className="muted">{emptyHint}</p>
      )}
    </div>
  );
}
