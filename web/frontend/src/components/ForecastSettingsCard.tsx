import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import {
  compass, configToForm, formToConfig, NEW_PANELS, type ForecastForm, type ForecastPayload, type ForecastSettings, type PanelForm,
} from '../lib/forecast';
import { kwh } from '../lib/format';
import { locale, t } from '../lib/i18n';
import { Card, Skeleton } from './common';

const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric' });

/** Forecast the evening before against what was made, for the last days (striped: forecast, solid: made). */
function Recent({ p }: { p: ForecastPayload }) {
  if (!p.recent.length) return <div class="setting-hint">{t('The comparison shows up here after a few days: each evening the forecast for the next day is noted.')}</div>;
  const top = Math.max(...p.recent.flatMap((r) => [r.forecast_kwh, r.made_kwh]), 0.1);
  return (
    <>
      <div class="fc-cmp" data-testid="forecast-recent">
        {p.recent.map((r) => (
          <div class="fc-cmp-row" key={r.date}>
            <span class="muted">{day(r.date)}</span>
            <div class="fc-cmp-bars">
              <span class="fa" style={{ width: `${(100 * r.forecast_kwh) / top}%` }} />
              <span class="aa" style={{ width: `${(100 * r.made_kwh) / top}%` }} />
            </div>
            <span class="fc-cmp-n">{kwh(r.forecast_kwh)} → {kwh(r.made_kwh)}</span>
          </div>
        ))}
      </div>
      <div class="fc-legend"><span class="ahead">{t('Forecast the evening before')}</span><span class="made">{t('Made')}</span></div>
      {p.miss_pct != null && <div class="setting-hint">{t('Typically off by ±{pct}% a day.', { pct: p.miss_pct })}</div>}
    </>
  );
}

/** Settings → General: where the panels are and how they are mounted; the forecast itself comes from Open-Meteo. */
export function ForecastSettingsCard() {
  const [cfg, setCfg] = useState<ForecastSettings | null>(null);
  const [p, setP] = useState<ForecastPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [f, setF] = useState<ForecastForm>(configToForm(null));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const c = await getJson<ForecastSettings>('/api/settings/forecast');
      setCfg(c); setF(configToForm(c.config)); setLoadError(null);
      getJson<ForecastPayload>('/api/forecast').then(setP).catch(() => setP(null));
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!cfg) return <Card title="Solar forecast">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={200} />}</Card>;

  const editable = cfg.web.can_change && !busy;
  const setPanel = (i: number, k: keyof PanelForm, v: string) => setF({ ...f, arrays: f.arrays.map((a, j) => (j === i ? { ...a, [k]: v } : a)) });
  const save = async (off = false) => {
    const body = off && cfg.config ? { ...cfg.config, lat: null, lon: null } : formToConfig(f);
    if (typeof body === 'string') { setError(body); return; }
    setBusy(true); setError(null); setSaved(null);
    try {
      const c = await postJson<ForecastSettings>('/api/settings/forecast', body);
      setCfg(c); setF(configToForm(c.config));
      setSaved(off ? t('The solar forecast is off.') : t('Saved. The forecast shows on the Overview in a minute.'));
      setTimeout(() => getJson<ForecastPayload>('/api/forecast').then(setP).catch(() => undefined), 5000);
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally { setBusy(false); }
  };

  return (
    <Card title="Solar forecast" class="fc-settings">
      <div class="setting-hint">
        {t('How much the panels should make today, tomorrow and the day after, from Open-Meteo’s weather forecast (free, no account). Only the location, rounded to about 1 km, is sent.')}
      </div>
      <div class={`tariff-now ${cfg.configured ? '' : 'muted'}`} data-testid="forecast-current">
        {cfg.configured && cfg.config
          ? <>{t('{kwp} kWp', { kwp: cfg.config.arrays.reduce((a, x) => a + x.kwp, 0).toLocaleString(locale()) })}
              <span class="muted"> · {cfg.config.arrays.map((a) => `${a.tilt}° ${compass(a.azimuth)}`).join(', ')}</span></>
          : t('Off')}
      </div>
      {cfg.error && <div class="field-error" role="status">{t(cfg.error)}</div>}
      {!cfg.web.can_change && <div class="setting-hint">{cfg.web.reason ? t(cfg.web.reason) : ''}</div>}

      <form class="inv-form fc-form" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <label class="fc-place"><span>{t('Location (latitude, longitude)')}</span>
          <input type="text" value={f.place} placeholder="50.45, 30.52" disabled={!editable} inputMode="decimal" spellcheck={false} data-testid="forecast-place"
                 onInput={(e) => setF({ ...f, place: (e.target as HTMLInputElement).value })} />
        </label>
        <label><span>{t('Losses, %')}</span>
          <input type="text" inputMode="numeric" value={f.losses} disabled={!editable} data-testid="forecast-losses"
                 onInput={(e) => setF({ ...f, losses: (e.target as HTMLInputElement).value })} />
        </label>
      </form>
      <div class="setting-hint">{t('In Google Maps, right-click the roof and click the numbers at the top to copy them. Losses: wiring, inverter and dust; 14% is usual.')}</div>

      {f.arrays.map((a, i) => (
        <div key={i} class="fc-group">
          <div class="export-label">{f.arrays.length > 1 ? t('Panels, group {n}', { n: i + 1 }) : t('Panels')}
            {f.arrays.length > 1 && <button type="button" class="link" disabled={!editable} onClick={() => setF({ ...f, arrays: f.arrays.filter((_, j) => j !== i) })}>{t('Remove')}</button>}
          </div>
          <div class="inv-form fc-form">
            <label><span>{t('Peak power, kWp')}</span>
              <input type="text" inputMode="decimal" value={a.kwp} placeholder="4.2" disabled={!editable} data-testid={`forecast-kwp-${i}`}
                     onInput={(e) => setPanel(i, 'kwp', (e.target as HTMLInputElement).value)} />
            </label>
            <label><span>{t('Tilt, °')}</span>
              <input type="text" inputMode="numeric" value={a.tilt} disabled={!editable} data-testid={`forecast-tilt-${i}`}
                     onInput={(e) => setPanel(i, 'tilt', (e.target as HTMLInputElement).value)} />
            </label>
            <label><span>{t('Facing, °')} <span class="muted">{Number.isFinite(Number(a.azimuth)) && a.azimuth !== '' ? `· ${compass(Number(a.azimuth))}` : ''}</span></span>
              <input type="text" inputMode="numeric" value={a.azimuth} disabled={!editable} data-testid={`forecast-azimuth-${i}`}
                     onInput={(e) => setPanel(i, 'azimuth', (e.target as HTMLInputElement).value)} />
            </label>
          </div>
        </div>
      ))}
      {f.arrays.length < cfg.max_arrays && (
        <button type="button" class="link" disabled={!editable} onClick={() => setF({ ...f, arrays: [...f.arrays, { ...NEW_PANELS, azimuth: '90' }] })} data-testid="forecast-add-group">
          + {t('Another group of panels (for example an east and a west roof)')}
        </button>
      )}
      <div class="setting-hint">{t('Peak power: the panels’ watts added up, ÷ 1000. Facing: 180 = south, 90 = east, 270 = west.')}</div>

      <label class="check">
        <input type="checkbox" checked={f.correct} disabled={!editable} onChange={() => setF({ ...f, correct: !f.correct })} data-testid="forecast-correct" />
        <span>{t('Correct the forecast by what the panels actually made')}
          <span class="setting-hint">{t('Covers shade, dirt and an inexact kWp. Hours with a full battery are left out, because the inverter may hold the panels back then.')}</span></span>
      </label>
      <label class="check">
        <input type="checkbox" checked={f.telegram} disabled={!editable} onChange={() => setF({ ...f, telegram: !f.telegram })} data-testid="forecast-telegram" />
        <span class="fc-threshold">{t('Telegram at 20:00 when tomorrow gives less than')}
          <input type="text" inputMode="numeric" value={f.below_pct} disabled={!editable || !f.telegram} aria-label={t('Percent of a usual day')} data-testid="forecast-below"
                 onInput={(e) => setF({ ...f, below_pct: (e.target as HTMLInputElement).value })} />
          {t('% of a usual day')}</span>
      </label>

      <div class="update-actions">
        <button type="button" class="btn btn-primary" disabled={!editable} onClick={() => save()} data-testid="forecast-save">{busy ? t('Saving…') : t('Save')}</button>
        {cfg.configured && <button type="button" class="btn" disabled={!editable} onClick={() => save(true)} data-testid="forecast-off">{t('Turn off')}</button>}
      </div>
      {error && <div class="field-error" role="alert" data-testid="forecast-error">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="forecast-saved">{saved}</div>}

      {cfg.configured && p?.configured && (
        <div class="fc-recent">
          <div class="export-label">{t('Last days: forecast vs made')}</div>
          <Recent p={p} />
          <div class="setting-hint">{p.factor != null
            ? t('Your panels made ×{k} of the plain forecast over the last 14 days ({n} sunny hours).', { k: p.factor.toLocaleString(locale()), n: p.factor_hours })
            : t('Not enough sunny hours recorded yet to correct the forecast ({n} of 20).', { n: p.factor_hours })}</div>
        </div>
      )}
      <div class="setting-hint">{t('While this is on, the server asks api.open-meteo.com for the forecast every hour. On the Overview, add the Solar forecast card with Customize.')}</div>
    </Card>
  );
}
