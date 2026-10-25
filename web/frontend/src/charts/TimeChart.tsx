import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import uPlot, { type AlignedData, type Options } from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { power, withUnit } from '../lib/format';
import { getLang, t, unit as unitName } from '../lib/i18n';
import { colors } from '../lib/tokens';
import type { HistoryPayload } from '../lib/api';

export type Unit = 'W' | '%' | 'V' | 'A' | 'Hz' | '°C';

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
  unit: Unit;
  /** Put on the right-hand axis (second unit). */
  right?: boolean;
  fill?: boolean;
}

export function fmt(v: number | null | undefined, unit: Unit): string {
  if (unit === 'W') return power(v);
  if (unit === '%') return withUnit(v, '%', 0);
  if (unit === 'Hz') return withUnit(v, 'Hz', 2);
  return withUnit(v, unit, 1);
}

const UK_TIME = new Intl.DateTimeFormat('uk-UA', { hour: '2-digit', minute: '2-digit' });
const UK_DAY = new Intl.DateTimeFormat('uk-UA', { day: '2-digit', month: '2-digit' });

/** Time-axis labels in Ukrainian, 24 h clock (uPlot's own formatter is English-only). */
function ukTimeValues(_u: uPlot, splits: number[], _axis: number, _space: number, incr: number): string[] {
  return splits.map((s) => {
    const d = new Date(s * 1000);
    return incr >= 86400 || (d.getHours() === 0 && d.getMinutes() === 0) ? UK_DAY.format(d) : UK_TIME.format(d);
  });
}

function axisFmt(unit: Unit) {
  return (_u: uPlot, splits: number[]) =>
    splits.map((v) => {
      if (v == null) return '';
      if (unit === 'W') {
        const big = splits.some((s) => Math.abs(s) >= 1000);
        const digits = splits.every((s) => s == null || Math.abs(s) % 100 < 1e-6 || Math.abs(s) % 100 > 100 - 1e-6) ? 1 : 2;   // 250 W steps need 0.25, not 0.3
        return big ? `${(v / 1000).toFixed(digits)} ${unitName('kW')}` : `${Math.round(v)} ${unitName('W')}`;
      }
      return `${+v.toFixed(2)} ${unitName(unit)}`;
    });
}

// Smallest visible span per unit, so flat lines (grid voltage, frequency) are not stretched to 0..400 V.
const MIN_SPAN: Record<Unit, number> = { W: 200, '%': 10, V: 4, A: 4, Hz: 0.2, '°C': 4 };

/** Auto-fit like Grafana: tight around the data, with padding; includes 0 when a zero line is drawn. */
export function fitRange(lo: number | null, hi: number | null, unit: Unit, includeZero = false,
                         minY?: number, maxY?: number): [number, number] {
  if (lo == null || hi == null || !Number.isFinite(lo) || !Number.isFinite(hi)) return [minY ?? 0, maxY ?? 1];
  if (includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  let span = hi - lo;
  if (span < MIN_SPAN[unit]) {
    const mid = (lo + hi) / 2;
    lo = mid - MIN_SPAN[unit] / 2; hi = mid + MIN_SPAN[unit] / 2;
    if (includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    span = hi - lo;
  }
  const pad = span * 0.08;
  const outLo = includeZero && lo >= 0 ? 0 : lo - pad;
  const outHi = includeZero && hi <= 0 ? 0 : hi + pad;
  return [minY ?? outLo, maxY ?? outHi];
}

function stats(vals: (number | null)[]) {
  let last: number | null = null, sum = 0, n = 0, max = -Infinity;
  for (const v of vals) {
    if (v == null) continue;
    last = v; sum += v; n++; if (v > max) max = v;
  }
  return { last, mean: n ? sum / n : null, max: n ? max : null };
}

const hexA = (hex: string, a: number) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
};

export function TimeChart({ data, series, height = 220, zeroLine = false, minY, maxY, font = 12, inline = false, fill = false }: {
  data: HistoryPayload; series: SeriesDef[]; height?: number; zeroLine?: boolean; minY?: number; maxY?: number;
  /** Axis label size in px; the axis widths scale with it (for the TV page). */
  font?: number;
  /** One-line legend with the latest values (wall display). */
  inline?: boolean;
  /** Take the height the parent leaves (the TV page keeps its columns level this way); `height` is then the fallback. */
  fill?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [cursorIdx, setCursorIdx] = useState<number | null>(null);

  const aligned = useMemo<AlignedData>(
    () => [data.t, ...series.map((s) => data.series[s.key] ?? data.t.map(() => null))] as AlignedData,
    [data, series],
  );
  const lang = getLang();                  // the plot is rebuilt when the language changes
  const leftUnit = series.find((s) => !s.right)!.unit;
  const rightUnit = series.find((s) => s.right)?.unit;

  useEffect(() => {
    const el = box.current!;
    const axis = { stroke: colors.muted, grid: { stroke: colors.border, width: 1 }, ticks: { stroke: colors.border, width: 1 },
                   font: `${font}px system-ui, sans-serif` };
    const k = font / 12;
    const opts: Options = {
      width: el.clientWidth || 600,
      height: fill ? el.clientHeight || height : height,
      legend: { show: false },
      cursor: { drag: { x: false, y: false }, points: { size: 7 } },
      scales: {
        x: { time: true },
        y: { range: (_u, lo, hi) => fitRange(lo, hi, leftUnit, zeroLine, minY, maxY) },
        ...(rightUnit ? { r: { range: (_u: uPlot, lo: number, hi: number) => fitRange(lo, hi, rightUnit, zeroLine) } } : {}),
      },
      axes: [
        { ...axis, space: 70 * k, size: 50 * k, ...(lang === 'uk' ? { values: ukTimeValues } : {}) },
        { ...axis, values: axisFmt(leftUnit), size: 76 * k },
        ...(rightUnit ? [{ ...axis, scale: 'r', side: 1, grid: { show: false }, values: axisFmt(rightUnit), size: 64 * k }] : []),
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: 2,
          scale: s.right ? 'r' : 'y',
          fill: s.fill ? hexA(s.color, 0.12) : undefined,
          points: { show: false },
        })),
      ],
      hooks: {
        setCursor: [(u) => setCursorIdx(u.cursor.idx ?? null)],
        draw: zeroLine ? [(u) => {
          const y = u.valToPos(0, 'y', true);
          if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) return;
          const ctx = u.ctx;
          ctx.save();
          ctx.strokeStyle = colors.muted;
          ctx.setLineDash([4, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(u.bbox.left, y);
          ctx.lineTo(u.bbox.left + u.bbox.width, y);
          ctx.stroke();
          ctx.restore();
        }] : [],
      },
    };
    plot.current = new uPlot(opts, aligned, el);
    const ro = new ResizeObserver(() => plot.current?.setSize({ width: el.clientWidth, height: fill ? el.clientHeight || height : height }));
    ro.observe(el);
    return () => { ro.disconnect(); plot.current?.destroy(); plot.current = null; };
    // Rebuild only when the series definition changes; data updates go through setData.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, height, fill, zeroLine, leftUnit, rightUnit, font, lang]);

  useEffect(() => { plot.current?.setData(aligned); }, [aligned]);

  const empty = data.t.length === 0;
  return (
    <div class={`tchart ${fill ? 'tchart-fill' : ''}`}>
      <div ref={box} class="tchart-plot" style={fill ? undefined : { minHeight: `${height}px` }} />
      {empty && <div class="tchart-empty">{t('No data in this range')}</div>}
      {inline ? (
        <div class="legend-inline">
          {series.map((s, i) => (
            <span key={s.key}><span class="swatch" style={{ background: s.color }} />{t(s.label)} <strong>{fmt(stats(aligned[i + 1] as (number | null)[]).last, s.unit)}</strong></span>
          ))}
        </div>
      ) : <table class="legend">
        <thead><tr><th /><th>{cursorIdx != null ? t('Cursor') : ''}</th><th>{t('Last')}</th><th>{t('Mean')}</th><th>{t('Max')}</th></tr></thead>
        <tbody>
          {series.map((s, i) => {
            const vals = aligned[i + 1] as (number | null)[];
            const st = stats(vals);
            return (
              <tr key={s.key}>
                <td><span class="swatch" style={{ background: s.color }} />{t(s.label)}</td>
                <td>{cursorIdx != null ? fmt(vals[cursorIdx], s.unit) : ''}</td>
                <td>{fmt(st.last, s.unit)}</td>
                <td>{fmt(st.mean, s.unit)}</td>
                <td>{fmt(st.max, s.unit)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>}
    </div>
  );
}
