import type { Fields, HistoryPayload } from '../lib/api';
import { share, tomorrowTip, weatherIcon, type ForecastPayload } from '../lib/forecast';
import { kwh } from '../lib/format';
import { locale, t, unit } from '../lib/i18n';
import { goTo } from '../lib/nav';
import { usePoll } from '../lib/poll';
import { Card, Skeleton } from './common';

const W = 640, H = 190, L = 34, B = 22, T = 14, R = 8;

/** Today and tomorrow by the hour: made (solid), forecast (striped), what was forecast for past hours (a tick), the usual load (dashed). */
function HourChart({ p, hours, made, now }: { p: ForecastPayload; hours: [number, number][]; made: HistoryPayload | null; now: number }) {
  const mid = new Date(now); mid.setHours(0, 0, 0, 0);
  const start = mid.getTime() / 1000;
  const cw = (W - L - R) / 48;
  const fc = new Map(hours);
  const actual = new Map<number, number>();
  if (made) made.t.forEach((x, i) => { const v = made.series.pv_power?.[i]; if (v != null) actual.set(x, v); });
  const nowH = Math.floor(now / 3_600_000) * 3600;
  const slots = Array.from({ length: 48 }, (_, i) => {
    const s = start + i * 3600;
    return { s, past: s < nowH, made: actual.get(s) ?? null, fc: fc.get(s) ?? null };
  });
  const load = p.load_by_hour;
  const top = Math.max(1000, ...slots.map((x) => Math.max(x.made ?? 0, x.fc ?? 0)), ...(load ?? []).map((v) => v ?? 0)) * 1.1;
  const y = (w: number) => T + (H - T - B) * (1 - w / top);
  const kwTop = top / 1000;
  const grid = [0, kwTop / 2, kwTop].map((v) => +v.toFixed(v < 2 ? 1 : 0));
  const xNow = L + ((now / 1000 - start) / 3600) * cw;
  const loadPts = load ? Array.from({ length: 48 }, (_, i) => load[i % 24] == null ? null : `${L + i * cw + cw / 2},${y(load[i % 24]!)}`).filter(Boolean).join(' ') : '';
  return (
    <svg viewBox={`0 0 ${W} ${H}`} class="fc-chart" role="img" aria-label={t('PV by hour, today and tomorrow')}>
      <defs>
        <pattern id="fc-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="3" height="6" class="fc-hatch" /></pattern>
      </defs>
      {grid.map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={y(g * 1000)} y2={y(g * 1000)} class="fc-grid" />
          <text x={L - 6} y={y(g * 1000) + 4} class="fc-axis" text-anchor="end">{g ? g.toLocaleString(locale()) : '0'}</text>
        </g>
      ))}
      <text x={L - 6} y={T - 4} class="fc-axis" text-anchor="end">{unit('kW')}</text>
      {slots.map((x, i) => {
        const bx = L + i * cw + 1.5, bw = cw - 3;
        if (x.past) {
          return (
            <g key={x.s}>
              {x.made != null && x.made > 0 && <rect x={bx} y={y(x.made)} width={bw} height={y(0) - y(x.made)} rx="2" class="fc-made" />}
              {x.fc != null && x.fc > 0 && <line x1={bx - 1} x2={bx + bw + 1} y1={y(x.fc)} y2={y(x.fc)} class="fc-tick" />}
            </g>
          );
        }
        return x.fc != null && x.fc > 0 ? <rect key={x.s} x={bx} y={y(x.fc)} width={bw} height={y(0) - y(x.fc)} rx="2" class="fc-ahead" fill="url(#fc-hatch)" /> : null;
      })}
      {loadPts && <polyline points={loadPts} class="fc-load" />}
      <line x1={L + 24 * cw} x2={L + 24 * cw} y1={T} y2={H - B} class="fc-grid" />
      <text x={L + 12 * cw} y={T} class="fc-axis fc-day" text-anchor="middle">{t('Today')}</text>
      <text x={L + 36 * cw} y={T} class="fc-axis fc-day" text-anchor="middle">{t('Tomorrow')}</text>
      <line x1={xNow} x2={xNow} y1={T + 6} y2={H - B} class="fc-now" />
      {[0, 6, 12, 18, 24, 30, 36, 42].map((h) => <text key={h} x={L + h * cw} y={H - 6} class="fc-axis">{`${String(h % 24).padStart(2, '0')}:00`}</text>)}
    </svg>
  );
}

const weekday = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric' });

/** Overview: what the panels should give today, tomorrow and the day after, by the hour, and when tomorrow is weak. */
export function ForecastCard({ p, error, fields, now }: { p: ForecastPayload | null; error: string | null; fields: Fields | null; now: number }) {
  const on = !!p?.configured;
  const hours = usePoll<{ configured: boolean; hours: [number, number][] }>(on ? '/api/forecast/hours' : null, 900_000).data;
  const made = usePoll<HistoryPayload>(on ? '/api/history?fields=pv_power&range=24h&every=1h&agg=mean' : null, 300_000).data;
  if (!p) return <Card title="Solar forecast">{error ? <div class="setting-hint">{t(error)}</div> : <Skeleton h={200} />}</Card>;
  if (!p.configured) {
    return (
      <Card title="Solar forecast">
        <div class="setting-hint">{t('How much the panels should make today, tomorrow and the day after, from the weather forecast and your panels’ own record.')}</div>
        <button type="button" class="link" onClick={() => goTo('settings', { tab: 'general' })}>{t('Set up')} →</button>
      </Card>
    );
  }
  const aside = (
    <span class="card-aside">
      {p.fetched_at && <span class="muted fc-updated">{t('updated {time}', { time: new Date(p.fetched_at).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) })}</span>}
      <button type="button" class="link" onClick={() => goTo('charts')}>{t('Charts')}</button>
    </span>
  );
  if (!p.days.length) {
    return <Card title="Solar forecast" aside={aside}><div class="setting-hint">{p.error ? t(p.error) : t('Waiting for the first forecast…')}</div></Card>;
  }
  const [today, tomorrow, after] = p.days;
  const madeToday = fields?.pv_energy_today ?? null;
  const tip = tomorrowTip(p);
  const pct = tomorrow ? share(tomorrow.kwh, p.usual_pv_kwh) : null;
  const weak = pct != null && pct < (p.below_pct ?? 50);
  return (
    <Card title="Solar forecast" aside={aside} class="fc-card">
      <div class="fc-days" data-testid="forecast-days">
        <div class="fc-day sel">
          <div class="fc-d"><span>{t('Today')}</span><span aria-hidden="true">{weatherIcon(today.cloud_pct)}</span></div>
          <div class="fc-v">{madeToday != null && today.rest_kwh != null ? kwh(madeToday + today.rest_kwh) : `≈ ${kwh(today.kwh)}`}</div>
          <div class="fc-s">{madeToday != null && today.rest_kwh != null
            ? t('{made} made · ≈ {rest} to come', { made: kwh(madeToday), rest: kwh(today.rest_kwh) }) : t('forecast for the whole day')}</div>
        </div>
        {tomorrow && (
          <div class="fc-day">
            <div class="fc-d"><span>{t('Tomorrow')}</span><span aria-hidden="true">{weatherIcon(tomorrow.cloud_pct)}</span></div>
            <div class="fc-v">≈ {kwh(tomorrow.kwh)}</div>
            <div class={`fc-s ${weak ? 'warn' : ''}`}>{pct != null ? t('{pct}% of a usual day', { pct }) : ' '}</div>
          </div>
        )}
        {after && (
          <div class="fc-day">
            <div class="fc-d"><span>{weekday(after.date)}</span><span aria-hidden="true">{weatherIcon(after.cloud_pct)}</span></div>
            <div class="fc-v">≈ {kwh(after.kwh)}</div>
            <div class="fc-s">{share(after.kwh, p.usual_pv_kwh) != null ? t('{pct}% of a usual day', { pct: share(after.kwh, p.usual_pv_kwh)! }) : ' '}</div>
          </div>
        )}
      </div>
      {hours ? <HourChart p={p} hours={hours.hours} made={made} now={now} /> : <Skeleton h={160} />}
      <div class="fc-legend">
        <span class="made">{t('Made')}</span><span class="ahead">{t('Forecast')}</span>
        {p.load_by_hour && <span class="load">{t('Usual load')}</span>}
        <span class="tick">{t('What was forecast')}</span>
      </div>
      {tip && <div class="update-result tone-warn" data-testid="forecast-tip">{tip}</div>}
      {p.stale && <div class="setting-hint">{p.error ? t(p.error) : t('The forecast could not be updated for a while: this is the last one.')}</div>}
      <div class="setting-hint fc-note">
        {t('Open-Meteo for {kwp} kWp.', { kwp: (p.kwp ?? 0).toLocaleString(locale()) })}{' '}
        {!p.correct ? t('Not corrected by your panels (off in Settings).')
          : p.factor != null ? t('Corrected by what your panels made in the last 14 days (×{k}).', { k: p.factor.toLocaleString(locale()) })
            : t('It is corrected by your panels once a few sunny days are recorded.')}
        {p.miss_pct != null && <> {t('Typically off by ±{pct}%.', { pct: p.miss_pct })}</>}
      </div>
    </Card>
  );
}
