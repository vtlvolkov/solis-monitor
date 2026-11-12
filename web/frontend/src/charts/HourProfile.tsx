import { t } from '../lib/i18n';
import type { HourRow } from '../lib/grid';
import { colors } from '../lib/tokens';

/** Voltage by hour of the day over the period: the min–max band, the mean line, and the standard's limits. */
export function HourProfile({ hours, lo, hi, width = 1200, height = 260 }: { hours: HourRow[]; lo: number; hi: number; width?: number; height?: number }) {
  const rows = hours.filter((h) => h.mean != null && h.min != null && h.max != null);
  if (rows.length < 2) return <div class="setting-hint">{t('No readings in this period.')}</div>;
  const W = width, H = height, padL = 36, padR = 8, padT = 8, padB = 22;
  const vals = rows.flatMap((h) => [h.min!, h.max!]);
  const y0 = Math.min(lo - 5, Math.floor(Math.min(...vals) / 5) * 5), y1 = Math.max(hi + 5, Math.ceil(Math.max(...vals) / 5) * 5);
  const sx = (hour: number) => padL + (hour / 23) * (W - padL - padR);
  const sy = (v: number) => padT + (1 - (v - y0) / (y1 - y0)) * (H - padT - padB);
  const band = `${rows.map((h) => `${sx(h.hour)},${sy(h.max!)}`).join(' ')} ${[...rows].reverse().map((h) => `${sx(h.hour)},${sy(h.min!)}`).join(' ')}`;
  const mean = rows.map((h) => `${sx(h.hour)},${sy(h.mean!)}`).join(' ');
  const yticks: number[] = [];
  for (let v = y0; v <= y1; v += 10) yticks.push(v);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} class="hour-profile" role="img" aria-label={t('Voltage by hour of day')} preserveAspectRatio="none">
      <rect x={padL} y={sy(hi)} width={W - padL - padR} height={sy(lo) - sy(hi)} fill={colors.ok} opacity="0.08" />
      {yticks.map((v) => (
        <g key={v}>
          <line x1={padL} x2={W - padR} y1={sy(v)} y2={sy(v)} stroke={colors.border} stroke-dasharray={v === lo || v === hi ? undefined : '2 4'} opacity={v === lo || v === hi ? 0.9 : 0.5} />
          <text x={padL - 6} y={sy(v) + 4} text-anchor="end" fill={colors.muted} font-size="11">{v}</text>
        </g>
      ))}
      <polygon points={band} fill={colors.grid} opacity="0.18" />
      <polyline points={mean} fill="none" stroke={colors.grid} stroke-width="2" />
      {rows.filter((h) => (h.out_pct ?? 0) >= 1).map((h) => (
        <circle key={h.hour} cx={sx(h.hour)} cy={sy(h.min!)} r="3" fill={colors.warn}><title>{t('{p} % of readings outside the standard', { p: h.out_pct!.toFixed(1) })}</title></circle>
      ))}
      {[0, 6, 12, 18, 23].map((hr) => (
        <text key={hr} x={sx(hr)} y={H - 6} text-anchor={hr === 0 ? 'start' : hr === 23 ? 'end' : 'middle'} fill={colors.muted} font-size="11">{`${String(hr).padStart(2, '0')}:00`}</text>
      ))}
    </svg>
  );
}
