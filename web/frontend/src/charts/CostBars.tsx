import { useEffect, useRef } from 'preact/hooks';
import { niceMax, stackMax, type CostDay, type Metric } from '../lib/cost';
import { t, unit } from '../lib/i18n';

const W = 900, H = 250, L = 62, B = 26, T = 10, R = 8;

/**
 * One stacked bar per day: night zone at the bottom, day zone on top, in cost or in kWh.
 * Click (or Enter) a bar to select that day.
 */
export function CostBars({ days, metric, selected, onSelect, currency }: {
  days: CostDay[]; metric: Metric; selected: string | null; onSelect: (date: string) => void; currency: string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {                      // narrow screens scroll sideways: start at the most recent days
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [days]);
  const max = niceMax(stackMax(days, metric, metric === 'cost' ? 1 : 0.5));
  const pw = W - L - R, ph = H - B - T;
  const slot = pw / Math.max(1, days.length);
  const bw = Math.max(3, slot * 0.64);
  const y = (v: number) => T + ph - (v / max) * ph;
  const every = days.length > 20 ? 2 : 1;
  const u = metric === 'cost' ? currency : unit('kWh');
  const fmt = (v: number) => (v >= 10 || Number.isInteger(v) ? String(Math.round(v * 10) / 10) : v.toFixed(2).replace(/0$/, ''));
  return (
    <div class="cost-scroll" ref={scroller}>
    <svg viewBox={`0 0 ${W} ${H}`} class="cost-bars" role="img" data-testid="cost-bars"
         aria-label={t('Daily grid import, day and night zones')}>
      {[0, 0.25, 0.5, 0.75, 1].map((k) => (
        <g key={k}>
          <line x1={L} x2={W - R} y1={y(k * max)} y2={y(k * max)} class="grid-line" />
          <text x={L - 6} y={y(k * max) + 4} class="axis-text" text-anchor="end">{fmt(k * max)} {u}</text>
        </g>
      ))}
      {days.map((d, i) => {
        const x = L + i * slot + (slot - bw) / 2;
        const night = d.has_data ? (metric === 'cost' ? d.night_cost : d.night_kwh) ?? 0 : 0;
        const day = d.has_data ? (metric === 'cost' ? d.day_cost : d.day_kwh) ?? 0 : 0;
        const on = selected === d.date;
        return (
          <g key={d.date} class={`cost-col ${on ? 'on' : ''} ${d.has_data ? '' : 'empty'}`} tabIndex={d.has_data ? 0 : -1}
             role="button" aria-pressed={on} aria-label={d.date} data-testid={`cost-day-${d.date}`}
             onClick={() => onSelect(d.date)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(d.date); } }}>
            <rect x={L + i * slot} y={T} width={slot} height={ph} class="cost-hit" />
            {night > 0 && <rect x={x} y={y(night)} width={bw} height={y(0) - y(night)} class="cost-night" rx={1.5} />}
            {day > 0 && <rect x={x} y={y(night + day)} width={bw} height={y(night) - y(night + day)} class="cost-day" rx={1.5} />}
            {i % every === 0 && <text x={L + i * slot + slot / 2} y={H - 8} class="axis-text" text-anchor="middle">{Number(d.date.slice(8))}</text>}
          </g>
        );
      })}
    </svg>
    </div>
  );
}
