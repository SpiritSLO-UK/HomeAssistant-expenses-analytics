// Tiny inline-SVG sparkline (no chart dependency). Shared by the Dashboard
// trends card and the Savings page.
//
// `width`/`height` define the SVG's *coordinate space* (viewBox) — the
// polyline/point math is computed in that space. The rendered element scales
// responsively to its container (width:100%, capped at the `width` prop) so it
// never overflows a narrow card / mobile screen, while keeping its aspect ratio.
export default function Sparkline({
  values,
  color = "#6aa9ff",
  width = 132,
  height = 34,
  label,
}: Readonly<{
  values: number[];
  color?: string;
  width?: number;
  height?: number;
  label?: string;
}>) {
  const pad = 3;
  // When a label is given the chart is meaningful (role="img"); otherwise it's
  // decorative next to the figures it illustrates (aria-hidden).
  const a11y = label ? { role: "img" as const, "aria-label": label } : { "aria-hidden": true };
  // Scales to the container but never larger than its natural coordinate size,
  // preserving the aspect ratio of the viewBox.
  const style = {
    display: "block",
    width: "100%",
    maxWidth: width,
    height: "auto",
  } as const;
  if (values.length < 2)
    return <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" style={style} {...a11y} />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const flat = max === min;
  const span = flat ? 1 : max - min;
  const xy = (v: number, i: number): [number, number] => [
    pad + (i / (values.length - 1)) * (width - 2 * pad),
    // A flat (all-equal) series draws through the vertical middle so it reads as
    // "steady" rather than being glued to the bottom (a 0 fraction).
    height - pad - (flat ? 0.5 : (v - min) / span) * (height - 2 * pad),
  ];
  const pts = values.map((v, i) => xy(v, i).map((n) => n.toFixed(1)).join(",")).join(" ");
  const [lx, ly] = xy(values[values.length - 1], values.length - 1);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" style={style} {...a11y}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={2.5} fill={color} />
    </svg>
  );
}
