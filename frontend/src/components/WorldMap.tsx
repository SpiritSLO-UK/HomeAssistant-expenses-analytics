import { WORLD_H, WORLD_LAND_PATH, WORLD_W } from "../data/world_land";
import { COUNTRY_CENTROIDS } from "../data/country_centroids";

// Distinct, theme-agnostic hues so a point on the map can be matched to its row
// in the legend by colour. The legend uses colorForIndex() with the same index.
export const MAP_PALETTE = [
  "#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899",
  "#14b8a6", "#f97316", "#6366f1", "#84cc16", "#06b6d4", "#e11d48",
];
export const colorForIndex = (i: number): string => MAP_PALETTE[i % MAP_PALETTE.length];

// "EU" is our EUR-currency fallback pseudo-code, not a real country — plot it at
// central Europe so eurozone spend still shows on the map.
const EXTRA_CENTROIDS: Record<string, [number, number]> = { EU: [50, 10] };

function centroidOf(code: string): [number, number] | null {
  return COUNTRY_CENTROIDS[code] ?? EXTRA_CENTROIDS[code] ?? null;
}

// Equirectangular projection — must match scripts/gen_worldmap.mjs so points sit
// on the coastline. [lat, lng] → [x, y] in the WORLD_W x WORLD_H viewBox.
function project(lat: number, lng: number): [number, number] {
  return [((lng + 180) / 360) * WORLD_W, ((90 - lat) / 180) * WORLD_H];
}

export interface MapPlot {
  code: string;
  name: string;
  total: number;
  count: number;
  color: string;
  /** Hash-router href for the country's transactions, or null if not drillable. */
  href: string | null;
}

const MIN_R = 6;
const MAX_R = 30;

function radiusFor(total: number, maxTotal: number): number {
  if (maxTotal <= 0) return MIN_R;
  return MIN_R + (MAX_R - MIN_R) * Math.sqrt(Math.max(0, total) / maxTotal);
}

function Bubble({ plot, maxTotal, money }: Readonly<{ plot: MapPlot; maxTotal: number; money: (n: number) => string }>) {
  const c = centroidOf(plot.code);
  if (!c) return null;
  const [x, y] = project(c[0], c[1]);
  const r = radiusFor(plot.total, maxTotal);
  const label = `${plot.name} · ${money(plot.total)} · ${plot.count} txn${plot.count === 1 ? "" : "s"}`;
  const circle = (
    <>
      {/* fill is a hex literal so it's safe as an attribute; the stroke (theme var)
          is set in CSS, since var()/color-mix() don't resolve in SVG attributes. */}
      <circle cx={x} cy={y} r={r} fill={plot.color} fillOpacity={0.8} />
      <title>{label}</title>
    </>
  );
  if (!plot.href) return <g>{circle}</g>;
  return (
    <a href={plot.href} className="worldmap__point" aria-label={`See ${plot.name} transactions`}>
      {circle}
    </a>
  );
}

/** A dependency-free equirectangular bubble map of spend by country. */
export default function WorldMap({
  plots,
  maxTotal,
  money,
}: Readonly<{ plots: MapPlot[]; maxTotal: number; money: (n: number) => string }>) {
  // Draw smallest-last so big bubbles don't hide small ones behind them.
  const ordered = [...plots].sort((a, b) => b.total - a.total);
  return (
    <div className="worldmap">
      <svg viewBox={`0 0 ${WORLD_W} ${WORLD_H}`} role="img" aria-label="World map of spending by country" preserveAspectRatio="xMidYMid meet">
        <rect className="worldmap__ocean" x={0} y={0} width={WORLD_W} height={WORLD_H} rx={6} />
        <path className="worldmap__land" d={WORLD_LAND_PATH} />
        {ordered.map((p) => (
          <Bubble key={p.code} plot={p} maxTotal={maxTotal} money={money} />
        ))}
      </svg>
    </div>
  );
}
