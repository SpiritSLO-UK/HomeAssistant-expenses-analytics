// Tiny inline-SVG sparkline (no chart dependency). Shared by the Dashboard
// trends card and the Savings page.

export default function Sparkline({
  values,
  color = "#6aa9ff",
  width = 132,
  height = 34,
}: {
  values: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  const pad = 3;
  if (values.length < 2) return <svg width={width} height={height} aria-hidden />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const xy = (v: number, i: number): [number, number] => [
    pad + (i / (values.length - 1)) * (width - 2 * pad),
    height - pad - ((v - min) / span) * (height - 2 * pad),
  ];
  const pts = values.map((v, i) => xy(v, i).map((n) => n.toFixed(1)).join(",")).join(" ");
  const [lx, ly] = xy(values[values.length - 1], values.length - 1);
  return (
    <svg width={width} height={height} aria-hidden style={{ display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={2.5} fill={color} />
    </svg>
  );
}
