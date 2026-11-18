// Solar forecast: payload types and pure helpers for the Overview card, the Settings card and the line on Charts.
// The poller fetches it from Open-Meteo every hour and corrects it by what the panels really made (poller/forecast.py).
import type { HistoryPayload } from './api';
import { kwh } from './format';
import { t } from './i18n';

export interface PanelGroup { kwp: number; tilt: number; azimuth: number }      // azimuth: compass degrees, 180 = south
export interface ForecastDay { date: string; kwh: number; rest_kwh: number | null; cloud_pct: number | null }
export interface RecentDay { date: string; forecast_kwh: number; made_kwh: number }

export interface ForecastPayload {
  configured: boolean;
  show: boolean;
  error: string | null;           // the last fetch's problem
  fetched_at: string | null;
  stale: boolean;                 // the last fetch failed or is hours old: this is the last good one
  kwp: number | null;
  arrays: PanelGroup[];
  losses: number | null;
  correct: boolean | null;        // the correction by the panels' own output is on
  factor: number | null;          // made ÷ forecast over the last two weeks (null until there are enough sunny hours)
  factor_hours: number;
  days: ForecastDay[];            // today, tomorrow, the day after
  usual_pv_kwh: number | null;    // an average recent day of the panels
  usual_load_kwh: number | null;  // an average day of the house
  load_by_hour: (number | null)[] | null;   // the usual load (W) by local hour, 0-23
  below_pct: number | null;
  recent: RecentDay[];            // the last days: forecast the evening before vs made
  miss_pct: number | null;        // how far off it has been, over those days
}

export interface ForecastConfig {
  lat: number | null; lon: number | null; arrays: PanelGroup[]; losses: number;
  correct: boolean; telegram: boolean; below_pct: number; overview: boolean;
}
export interface ForecastSettings {
  config: ForecastConfig | null; configured: boolean; error: string | null; fetched_at: string | null; max_arrays: number;
  web: { can_change: boolean; reason: string | null };
}

/** The form as typed: strings, so a half-typed number is not lost. */
export interface PanelForm { kwp: string; tilt: string; azimuth: string }
export interface ForecastForm { place: string; arrays: PanelForm[]; losses: string; correct: boolean; telegram: boolean; below_pct: string; overview: boolean }

export const NEW_PANELS: PanelForm = { kwp: '', tilt: '30', azimuth: '180' };

/** "50.4501, 30.5234" (as Google Maps copies it), "50.45 30.52" or "50.45; 30.52". */
export function parsePlace(s: string): { lat: number; lon: number } | null {
  const m = /^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$/.exec(s);
  if (!m) return null;
  const lat = Number(m[1]), lon = Number(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

const num = (s: string): number | null => (s.trim() !== '' && Number.isFinite(Number(s.replace(',', '.'))) ? Number(s.replace(',', '.')) : null);

export function formToConfig(f: ForecastForm): ForecastConfig | string {
  const place = parsePlace(f.place);
  if (!place) return t('Enter the location as two numbers, like 50.45, 30.52.');
  const arrays: PanelGroup[] = [];
  for (const a of f.arrays) {
    const kwp = num(a.kwp), tilt = num(a.tilt), az = num(a.azimuth);
    if (kwp == null || kwp < 0.1 || kwp > 200) return t('Peak power is in kWp, from 0.1 to 200 (the sum of the panels’ watts ÷ 1000).');
    if (tilt == null || tilt < 0 || tilt > 90) return t('Tilt is from 0° (flat) to 90° (upright).');
    if (az == null || az < 0 || az > 360) return t('Facing is a compass direction from 0 to 360 (180 = south).');
    arrays.push({ kwp, tilt: Math.round(tilt), azimuth: Math.round(az) % 360 });
  }
  const losses = num(f.losses), below = num(f.below_pct);
  if (losses == null || losses < 0 || losses > 50) return t('Losses are from 0 to 50%.');
  if (below == null || below < 10 || below > 100) return t('The Telegram threshold is from 10 to 100%.');
  return { lat: place.lat, lon: place.lon, arrays, losses: Math.round(losses), correct: f.correct, telegram: f.telegram,
           below_pct: Math.round(below), overview: f.overview };
}

export function configToForm(c: ForecastConfig | null): ForecastForm {
  if (!c) return { place: '', arrays: [{ ...NEW_PANELS }], losses: '14', correct: true, telegram: false, below_pct: '50', overview: true };
  return {
    place: c.lat != null && c.lon != null ? `${c.lat}, ${c.lon}` : '',
    arrays: c.arrays.map((a) => ({ kwp: String(a.kwp), tilt: String(a.tilt), azimuth: String(a.azimuth) })),
    losses: String(c.losses), correct: c.correct, telegram: c.telegram, below_pct: String(c.below_pct), overview: c.overview,
  };
}

const POINTS = ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'] as const;
/** 180 -> "south", 100 -> "east" (the nearest of eight). */
export const compass = (deg: number): string => t(POINTS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]);

export function weatherIcon(cloud: number | null): string {
  if (cloud == null) return '';
  return cloud < 25 ? '☀️' : cloud < 50 ? '🌤️' : cloud < 80 ? '⛅' : '☁️';
}

/** "≈ 35% of a usual day" for a day's forecast against the recent average. */
export function share(kwh: number, usual: number | null): number | null {
  return usual && usual > 0 ? Math.round((100 * kwh) / usual) : null;
}

/** The warning under the chart: tomorrow is weak against the usual day (the Telegram threshold). */
export function tomorrowTip(p: ForecastPayload): string | null {
  const d = p.days[1];
  const pct = d ? share(d.kwh, p.usual_pv_kwh) : null;
  if (!d || pct == null || pct >= (p.below_pct ?? 50)) return null;
  const head = t('Tomorrow the panels give ≈ {energy}, {pct}% of a usual day.', { energy: kwh(d.kwh), pct });
  return p.usual_load_kwh ? `${head} ${t('The house usually uses ≈ {energy} a day: charge the battery tonight if you need it full.', { energy: kwh(p.usual_load_kwh) })}` : head;
}

/**
 * The forecast as a series of the history payload, `pv_forecast`, read off the hourly values at the payload's own
 * times (each hour's value sits at its middle, straight lines between). With `until` (the end of today on the day
 * view) the times run on past the last reading, so the line shows the rest of the day.
 */
export function withForecast(h: HistoryPayload, hours: [number, number][], until?: number): HistoryPayload {
  if (!hours.length) return h;
  const xs = hours.map(([s]) => s + 1800), ys = hours.map(([, w]) => w);
  const at = (x: number): number | null => {
    if (x < xs[0] - 1800 || x > xs[xs.length - 1] + 1800) return null;
    if (x <= xs[0]) return ys[0];
    if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
    let lo = 0, hi = xs.length - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] <= x) lo = m; else hi = m; }
    if (xs[hi] - xs[lo] > 3600) return null;                                   // a gap: no forecast then
    return ys[lo] + ((ys[hi] - ys[lo]) * (x - xs[lo])) / (xs[hi] - xs[lo]);
  };
  let ts = h.t;
  if (until != null && ts.length) {
    const step = ts.length > 1 ? Math.max(300, ts[ts.length - 1] - ts[ts.length - 2]) : 300;
    const extra: number[] = [];
    for (let x = ts[ts.length - 1] + step; x <= until; x += step) extra.push(x);
    ts = [...ts, ...extra];
  }
  const pad = ts.length - h.t.length;
  const series: HistoryPayload['series'] = {};
  for (const [k, v] of Object.entries(h.series)) series[k] = pad ? [...v, ...Array(pad).fill(null)] : v;
  series.pv_forecast = ts.map((x) => { const v = at(x); return v == null ? null : Math.round(v); });
  return { ...h, t: ts, series };
}
