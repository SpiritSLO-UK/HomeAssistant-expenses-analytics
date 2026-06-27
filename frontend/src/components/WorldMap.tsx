import { useEffect, useMemo, useRef, useState } from "react";
import {
  WORLD_COUNTRY_PATHS,
  WORLD_H,
  WORLD_LAT_BOTTOM,
  WORLD_LAT_TOP,
  WORLD_W,
} from "../data/world_land";
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

// Cropped equirectangular projection — must match scripts/gen_worldmap.mjs so
// points sit on the map. [lat, lng] -> [x, y] in the WORLD_W x WORLD_H viewBox.
function project(lat: number, lng: number): [number, number] {
  return [
    ((lng + 180) / 360) * WORLD_W,
    ((WORLD_LAT_TOP - lat) / (WORLD_LAT_TOP - WORLD_LAT_BOTTOM)) * WORLD_H,
  ];
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
const MAX_R = 26;
const MAX_ZOOM = 8;

function radiusFor(total: number, maxTotal: number): number {
  if (maxTotal <= 0) return MIN_R;
  return MIN_R + (MAX_R - MIN_R) * Math.sqrt(Math.max(0, total) / maxTotal);
}

interface View {
  k: number;
  x: number;
  y: number;
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

// Keep the scaled map covering the viewport (no blank gutters).
function clampTranslate(x: number, y: number, k: number): [number, number] {
  return [clamp(x, WORLD_W * (1 - k), 0), clamp(y, WORLD_H * (1 - k), 0)];
}

// Zoom by `factor` keeping the point (px, py) — in viewBox coords — fixed.
function zoomAt(v: View, factor: number, px: number, py: number): View {
  const k = clamp(v.k * factor, 1, MAX_ZOOM);
  const wx = (px - v.x) / v.k;
  const wy = (py - v.y) / v.k;
  const [x, y] = clampTranslate(px - k * wx, py - k * wy, k);
  return { k, x, y };
}

function Bubble({
  plot, maxTotal, k, money,
}: Readonly<{ plot: MapPlot; maxTotal: number; k: number; money: (n: number) => string }>) {
  const c = centroidOf(plot.code);
  if (!c) return null;
  const [x, y] = project(c[0], c[1]);
  const r = radiusFor(plot.total, maxTotal) / k; // counter-scale so points stay a constant size
  const label = `${plot.name} · ${money(plot.total)} · ${plot.count} txn${plot.count === 1 ? "" : "s"}`;
  const circle = (
    <>
      {/* fill is a hex literal (safe as an attribute); the stroke (theme var) is in CSS */}
      <circle cx={x} cy={y} r={r} fill={plot.color} fillOpacity={0.8} vectorEffect="non-scaling-stroke" />
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

/** A dependency-free, zoomable equirectangular map of spend by country. */
export default function WorldMap({
  plots, maxTotal, money,
}: Readonly<{ plots: MapPlot[]; maxTotal: number; money: (n: number) => string }>) {
  const [view, setView] = useState<View>({ k: 1, x: 0, y: 0 });
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // Pointer position in viewBox coords from a mouse/wheel event.
  const toViewBox = (clientX: number, clientY: number): [number, number] => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return [WORLD_W / 2, WORLD_H / 2];
    return [((clientX - rect.left) / rect.width) * WORLD_W, ((clientY - rect.top) / rect.height) * WORLD_H];
  };

  // Wheel zoom — attached natively so we can preventDefault (stop the page scrolling).
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [px, py] = toViewBox(e.clientX, e.clientY);
      setView((v) => zoomAt(v, e.deltaY < 0 ? 1.15 : 1 / 1.15, px, py));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const zoomButton = (factor: number) =>
    setView((v) => zoomAt(v, factor, WORLD_W / 2, WORLD_H / 2));
  const reset = () => setView({ k: 1, x: 0, y: 0 });

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (view.k <= 1) return;
    drag.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!drag.current || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const dx = ((e.clientX - drag.current.sx) / rect.width) * WORLD_W;
    const dy = ((e.clientY - drag.current.sy) / rect.height) * WORLD_H;
    const [x, y] = clampTranslate(drag.current.ox + dx, drag.current.oy + dy, view.k);
    setView((v) => ({ ...v, x, y }));
  };
  const endDrag = () => {
    drag.current = null;
    setDragging(false);
  };

  // Draw smallest-last so big bubbles don't hide small ones behind them.
  // Memoised so dragging/zooming (which re-render on every frame) doesn't re-sort.
  const ordered = useMemo(() => [...plots].sort((a, b) => b.total - a.total), [plots]);
  const zoomed = view.k > 1;
  let cursorClass: string | undefined;
  if (dragging) cursorClass = "is-dragging";
  else if (zoomed) cursorClass = "is-zoomed";

  return (
    <div className="worldmap">
      <div className="worldmap__controls">
        <button type="button" className="btn btn--sm btn--ghost" onClick={() => zoomButton(1.5)} aria-label="Zoom in">＋</button>
        <button type="button" className="btn btn--sm btn--ghost" onClick={() => zoomButton(1 / 1.5)} aria-label="Zoom out" disabled={!zoomed}>－</button>
        <button type="button" className="btn btn--sm btn--ghost" onClick={reset} disabled={!zoomed}>Reset</button>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WORLD_W} ${WORLD_H}`}
        role="img"
        aria-label="World map of spending by country"
        preserveAspectRatio="xMidYMid meet"
        className={cursorClass}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          <rect className="worldmap__ocean" x={0} y={0} width={WORLD_W} height={WORLD_H} />
          {WORLD_COUNTRY_PATHS.map((d) => (
            <path key={d} className="worldmap__country" d={d} vectorEffect="non-scaling-stroke" />
          ))}
          {ordered.map((p) => (
            <Bubble key={p.code} plot={p} maxTotal={maxTotal} k={view.k} money={money} />
          ))}
        </g>
      </svg>
    </div>
  );
}
