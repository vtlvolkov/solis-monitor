import { t } from '../lib/i18n';
import { outOfBand, type Bin } from '../lib/grid';
import { colors } from '../lib/tokens';

/**
 * A histogram of readings (voltage or frequency) as inline SVG: bars per bin, the standard's band shaded, and the
 * nominal value marked. Bars outside the band are drawn in the warning colour so the eye lands on them.
 */
export function Histogram({ bins, width, lo, hi, nominal, unit, decimals = 0, height = 180 }: {
  bins: Bin[]; width: number; lo: number; hi: number; nominal: number; unit: string; decimals?: number; height?: number;
}) {
  if (!bins.length) return <div class="setting-hint">{t('No readings in this period.')}</div>;
  const W = 600, H = height, padL = 8, padR = 8, padB = 22, padT = 6;
  const x0 = Math.min(bins[0].from, lo - width * 2), x1 = Math.max(bins[bins.length - 1].from + width, hi + width * 2);
  const total = bins.reduce((a, b) => a + b.count, 0) || 1;
  const max = Math.max(...bins.map((b) => b.count)) || 1;
  const sx = (v: number) => padL + ((v - x0) / (x1 - x0)) * (W - padL - padR);
  const sy = (c: number) => padT + (1 - c / max) * (H - padT - padB);
  const ticks: number[] = [];
  const step = decimals ? 0.1 : 10;
  for (let v = Math.ceil(x0 / step) * step; v <= x1; v += step) ticks.push(Number(v.toFixed(decimals + 1)));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} class="histogram" role="img" aria-label={t('Distribution of readings')} preserveAspectRatio="none">
      <rect x={sx(lo)} y={padT} width={sx(hi) - sx(lo)} height={H - padT - padB} fill={colors.ok} opacity="0.08" />
      {bins.map((b) => {
        const out = outOfBand(b.from, width, lo, hi);
        const bw = Math.max(1, sx(b.from + width) - sx(b.from) - 1);
        return <rect key={b.from} x={sx(b.from)} y={sy(b.count)} width={bw} height={H - padB - sy(b.count)}
                     fill={out ? colors.warn : colors.grid} opacity={out ? 0.95 : 0.8}>
          <title>{`${b.from.toFixed(decimals)}–${(b.from + width).toFixed(decimals)} ${unit}: ${(100 * b.count / total).toFixed(1)} %`}</title>
        </rect>;
      })}
      <line x1={sx(nominal)} x2={sx(nominal)} y1={padT} y2={H - padB} stroke={colors.text} stroke-dasharray="3 3" opacity="0.5" />
      <line x1={padL} x2={W - padR} y1={H - padB} y2={H - padB} stroke={colors.border} />
      {ticks.map((v) => (
        <text key={v} x={sx(v)} y={H - 6} text-anchor="middle" fill={colors.muted} font-size="11">{v.toFixed(decimals)}</text>
      ))}
      <text x={sx(lo) + 4} y={padT + 12} fill={colors.ok} font-size="11">{lo.toFixed(decimals)} {unit}</text>
      <text x={sx(hi) - 4} y={padT + 12} fill={colors.ok} font-size="11" text-anchor="end">{hi.toFixed(decimals)} {unit}</text>
    </svg>
  );
}
