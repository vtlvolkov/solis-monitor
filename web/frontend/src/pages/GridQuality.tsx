import { useState } from 'preact/hooks';
import { Card, ErrorBanner, Header, Skeleton } from '../components/common';
import { Histogram } from '../charts/Histogram';
import { HourProfile } from '../charts/HourProfile';
import { useFetch } from './Charts';
import {
  GRID_RANGES, LIMIT_BOUNDS, LIMIT_NAMES, RANGE_LABELS, STANDARD, V_NOMINAL, fmtHz, fmtMinutes, fmtPct, fmtV, hzBand, isStandard,
  limitsError, loadLimits, qualityUrl, saveLimits, vBand, verdict, type GridQualityPayload, type GridRange, type Limits,
} from '../lib/grid';
import { locale, t } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import { goTo } from '../lib/nav';

const dayLabel = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString(locale(), { weekday: 'short', day: 'numeric', month: 'short' });
const dur = (s: number) => (s < 60 ? t('{n} min', { n: 0 }) : fmtMinutes(s / 60));

function readRange(): GridRange {
  const r = new URLSearchParams(location.search).get('range');
  return (GRID_RANGES as string[]).includes(r ?? '') ? (r as GridRange) : '7d';
}

function Tile({ title, value, sub, tone, testId }: { title: string; value: string; sub?: string; tone?: 'ok' | 'warn' | 'bad'; testId?: string }) {
  return (
    <div class={`stat ${tone ? `tone-${tone}` : ''}`} data-testid={testId}>
      <div class="stat-title">{title}</div>
      <div class="stat-value">{value}</div>
      {sub && <div class="stat-sub">{sub}</div>}
    </div>
  );
}

/**
 * Grid quality: how well the grid kept to the standard (230 V ±10 %, 50 Hz ±0.2 Hz) over the period. Everything
 * is computed on the server from the stored readings (/api/grid/quality); this page only draws it.
 */
export function GridQuality({ live, now }: { live: LiveState; now: number }) {
  const [range, setRange] = useState<GridRange>(readRange);
  const [limits, setLimits] = useState<Limits>(loadLimits);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<keyof Limits, string>>({ v_min: '', v_max: '', hz_min: '', hz_max: '' });
  const [draftError, setDraftError] = useState<string | null>(null);
  const pick = (r: GridRange) => { setRange(r); const p = new URLSearchParams(location.search); p.set('range', r); history.replaceState(null, '', `?${p}`); };
  const { data, error, loading } = useFetch<GridQualityPayload>(qualityUrl(range, limits), 300_000);
  const d = data;
  const v = d ? verdict(d.out.v_pct, d.outages.count) : null;
  const L = d?.limits ?? limits;
  const openLimits = () => { setDraft({ v_min: String(limits.v_min), v_max: String(limits.v_max), hz_min: String(limits.hz_min), hz_max: String(limits.hz_max) }); setDraftError(null); setEditing(true); };
  const applyLimits = (next?: Limits) => {
    const l = next ?? { v_min: Number(draft.v_min), v_max: Number(draft.v_max), hz_min: Number(draft.hz_min), hz_max: Number(draft.hz_max) };
    const bad = limitsError(l);
    if (bad) { setDraftError(bad); return; }
    saveLimits(l); setLimits(l); setEditing(false);
  };

  return (
    <div class="page grid-quality">
      <Header live={live} now={now} title="Grid quality" />
      <div class="controls">
        <div class="controls-group">
          <button type="button" class="link" onClick={() => goTo('events')} data-testid="grid-back">← {t('Events')}</button>
          <div class="seg" role="group" aria-label={t('Period')}>
            {GRID_RANGES.map((r) => (
              <button type="button" key={r} class={r === range ? 'on' : ''} aria-pressed={r === range} onClick={() => pick(r)} data-testid={`grid-range-${r}`}>{t(RANGE_LABELS[r])}</button>
            ))}
          </div>
        </div>
        <div class="controls-group">
          {loading && <span class="spinner" aria-label={t('Loading')} />}
          <button type="button" class="btn btn-small" onClick={editing ? () => setEditing(false) : openLimits} data-testid="grid-limits">
            {t('Limits')}: {vBand(limits)} · {hzBand(limits)}{isStandard(limits) ? '' : ' *'}
          </button>
        </div>
      </div>
      {editing && (
        <Card class="grid-limits" title="Limits">
          <div class="setting-hint">{t('What counts as "within the standard" on this page. The supply standard (EN 50160 / ДСТУ) is 230 V ±10 % and 50 Hz ±0.2 Hz. Saved in this browser; the server recomputes everything on this page with these limits.')}</div>
          <div class="grid-limits-form">
            {(Object.keys(LIMIT_NAMES) as (keyof Limits)[]).map((k) => (
              <label class="field" key={k}>
                <span>{t(LIMIT_NAMES[k])} <span class="muted">({k.startsWith('v') ? t('V') : t('Hz')}, {LIMIT_BOUNDS[k][0]}–{LIMIT_BOUNDS[k][1]})</span></span>
                <input type="number" step={k.startsWith('v') ? 1 : 0.01} min={LIMIT_BOUNDS[k][0]} max={LIMIT_BOUNDS[k][1]} value={draft[k]}
                       onInput={(e) => setDraft({ ...draft, [k]: (e.target as HTMLInputElement).value })} data-testid={`grid-limit-${k}`} />
              </label>
            ))}
          </div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-primary" onClick={() => applyLimits()} data-testid="grid-limits-apply">{t('Apply')}</button>
            <button type="button" class="btn" onClick={() => applyLimits(STANDARD)} data-testid="grid-limits-standard">{t('Back to the standard')}</button>
            <button type="button" class="btn" onClick={() => setEditing(false)}>{t('Cancel')}</button>
          </div>
          {draftError && <div class="field-error" role="alert" data-testid="grid-limits-error">{draftError}</div>}
        </Card>
      )}
      {error && <ErrorBanner message={error} stale={!!d} />}

      {!d ? <div class="stat-row">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={96} />)}</div> : (
        <div class="stat-row" data-testid="grid-tiles">
          <Tile title={t('Voltage')} value={fmtV(d.voltage.mean)} sub={`${fmtV(d.voltage.min)} – ${fmtV(d.voltage.max)}`} />
          <Tile title={t('Outside {band}', { band: vBand(L) })} value={fmtPct(d.out.v_pct)} sub={t('{time} in total', { time: fmtMinutes(d.out.v_min) })} tone={d.out.v_pct >= 5 ? 'bad' : d.out.v_pct >= 0.5 ? 'warn' : 'ok'} testId="grid-out-v" />
          <Tile title={t('Sags / surges')} value={`${d.out.sags} / ${d.out.surges}`} sub={t('below {lo} V / above {hi} V', { lo: L.v_min, hi: L.v_max })} tone={d.out.sags + d.out.surges > 0 ? 'warn' : 'ok'} />
          <Tile title={t('Frequency')} value={fmtHz(d.frequency.mean)} sub={`${fmtHz(d.frequency.min)} – ${fmtHz(d.frequency.max)}`} />
          <Tile title={t('Outside {band}', { band: hzBand(L) })} value={fmtPct(d.out.hz_pct)} sub={t('{time} in total', { time: fmtMinutes(d.out.hz_min) })} tone={d.out.hz_pct >= 1 ? 'warn' : 'ok'} />
          <Tile title={t('Outages')} value={String(d.outages.count)} sub={d.outages.count ? t('{total} total · longest {longest}', { total: dur(d.outages.total_s), longest: dur(d.outages.longest_s) }) : t('none in this period')} tone={d.outages.count ? 'warn' : 'ok'} />
        </div>
      )}
      {v && <div class={`update-result tone-${v.tone} grid-verdict`} role="status" data-testid="grid-verdict">{v.text} <span class="muted">· {t('{p} % of readings outside {band}', { p: d!.out.v_pct.toFixed(1), band: vBand(L) })}</span></div>}

      <div class="chart-grid">
        {!d ? <Card title="Voltage distribution"><Skeleton h={180} /></Card> : (
          <Card title="Voltage distribution" aside={<span class="band-note">{t('Shaded: within the standard')}</span>}>
            <Histogram bins={d.voltage.bins} width={d.voltage.bin_width} lo={L.v_min} hi={L.v_max} nominal={V_NOMINAL} unit={t('V')} />
            <div class="setting-hint">{t('5 % of readings are below {p05}, 5 % above {p95}. Dashed line: 230 V.', { p05: fmtV(d.voltage.p05), p95: fmtV(d.voltage.p95) })}</div>
          </Card>
        )}
        {!d ? <Card title="Frequency distribution"><Skeleton h={180} /></Card> : (
          <Card title="Frequency distribution" aside={<span class="band-note">{t('Shaded: within the standard')}</span>}>
            <Histogram bins={d.frequency.bins} width={d.frequency.bin_width} lo={L.hz_min} hi={L.hz_max} nominal={50} unit={t('Hz')} decimals={2} />
          </Card>
        )}
        <div class="span-2">
          {!d ? <Card title="Voltage by hour of day"><Skeleton h={200} /></Card> : (
            <Card title="Voltage by hour of day" aside={<span class="band-note">{t('Band: lowest to highest reading · line: average · dots: hours often outside the standard')}</span>}>
              <HourProfile hours={d.hours} lo={L.v_min} hi={L.v_max} />
            </Card>
          )}
        </div>
        <div class="span-2">
          <Card title="Day by day">
            {!d ? <Skeleton h={200} /> : (
              <div class="table-wrap">
                <table class="cost-table grid-table" data-testid="grid-days">
                  <thead>
                    <tr>
                      <th>{t('Day')}</th><th>{t('Min')}</th><th>{t('Average')}</th><th>{t('Max')}</th>
                      <th>{t('Outside {band}', { band: vBand(L) })}</th><th>{t('Sags')}</th><th>{t('Surges')}</th><th>{t('Outside {band}', { band: hzBand(L) })}</th><th>{t('Outages')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.days.map((r) => (
                      <tr key={r.date} class={r.readings ? (r.out_min >= 30 || r.outages ? 'warn' : '') : 'muted'}>
                        <td>{dayLabel(r.date)}</td>
                        {r.readings ? (
                          <>
                            <td class={r.min_v != null && r.min_v < L.v_min ? 'bad' : ''}>{fmtV(r.min_v)}</td>
                            <td>{fmtV(r.mean_v)}</td>
                            <td class={r.max_v != null && r.max_v > L.v_max ? 'bad' : ''}>{fmtV(r.max_v)}</td>
                            <td>{r.out_min ? fmtMinutes(r.out_min) : '—'}</td>
                            <td>{r.sags || '—'}</td>
                            <td>{r.surges || '—'}</td>
                            <td>{r.hz_out_min ? fmtMinutes(r.hz_out_min) : '—'}</td>
                            <td>{r.outages ? `${r.outages} · ${dur(r.outage_s)}` : '—'}</td>
                          </>
                        ) : <td colSpan={8}>{t('no readings')}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div class="setting-hint">{t('A sag or surge is one excursion below {lo} V or above {hi} V lasting at least one reading. Readings during an outage are left out; the standard allows 10 % deviation for 95 % of the time.', { lo: L.v_min, hi: L.v_max })}</div>
          </Card>
        </div>
      </div>
    </div>
  );
}
