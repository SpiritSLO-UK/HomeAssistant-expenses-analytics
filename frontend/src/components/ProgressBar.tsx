// Shared progress bar for Budgets, Projects and Savings goals. One geometry
// (height 8, radius 4) and a single track colour; the caller supplies the fill
// colour so each page keeps its own status/percent logic. Presentational only.
export default function ProgressBar({
  percent,
  color,
  title,
}: Readonly<{
  percent: number;
  color: string;
  title?: string;
}>) {
  return (
    <div
      style={{
        marginTop: 6,
        height: 8,
        borderRadius: 4,
        background: "rgba(127,127,127,0.22)",
        overflow: "hidden",
      }}
      title={title}
    >
      <div style={{ width: `${Math.min(percent, 100)}%`, height: "100%", background: color }} />
    </div>
  );
}
