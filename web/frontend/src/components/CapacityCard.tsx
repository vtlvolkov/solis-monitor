import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { describeSource, fallback, parseCapacity, type CapacityPayload } from '../lib/capacity';
import { t, unit } from '../lib/i18n';
import { Card, Skeleton } from './common';

type Review = { kwh: number | null; summary: string };

/**
 * Usable battery capacity for the backup-runtime estimate and outage alerts, for when a battery moves to another
 * inverter. It is the dashboard's own value (kept by the poller): nothing is written to the inverter.
 */
export function CapacityCard() {
  const [p, setP] = useState<CapacityPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getJson<CapacityPayload>('/api/settings/battery-capacity');
      setP(data);
      setLoadError(null);
      setValue(data.capacity_kwh != null ? String(data.capacity_kwh) : '');
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!p) {
    return <Card title="Battery capacity">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={120} />}</Card>;
  }

  const editable = p.web.can_change && !busy;
  const kwh = unit('kWh');
  const now = p.capacity_kwh != null ? `${p.capacity_kwh} ${kwh}` : '--';

  const onReview = (e: Event) => {
    e.preventDefault();
    const r = parseCapacity(value, p.max_kwh, p.capacity_kwh);
    if (!r.ok) { setReview(null); return setError(r.error); }
    setError(null);
    setReview({ kwh: r.value, summary: t('Battery capacity: {from} → {to}', { from: now, to: `${r.value} ${kwh}` }) });
  };

  const apply = async () => {
    if (!review) return;
    setBusy(true);
    try {
      const answer = await postJson<CapacityPayload>('/api/settings/battery-capacity', { kwh: review.kwh });
      setP(answer);
      setValue(answer.capacity_kwh != null ? String(answer.capacity_kwh) : '');
      setSaved(t('Saved. Runtime estimates and outage alerts now use {n}.', { n: answer.capacity_kwh != null ? `${answer.capacity_kwh} ${kwh}` : '--' }));
      setReview(null);
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Battery capacity">
      <div class="tariff-now" data-testid="capacity-now">
        {now} <span class="muted">({describeSource(p)})</span>
      </div>
      <div class="setting-hint">
        {t('Usable energy of the battery connected to this inverter, for the backup-runtime estimate and outage alerts. Change it when you move a battery. Nothing is written to the inverter.')}
      </div>
      {!p.web.can_change && <div class="setting-hint">{p.web.reason ? t(p.web.reason) : ''}</div>}

      <form class="tariff-form" onSubmit={onReview} data-testid="capacity-form">
        <label><span>{t('Usable capacity')} <span class="muted">{kwh}</span></span>
          <input type="text" inputMode="decimal" value={value} disabled={!editable} data-testid="capacity-kwh"
                 onInput={(e) => { setValue((e.target as HTMLInputElement).value); setReview(null); setError(null); setSaved(null); }} /></label>
        <div class="update-actions">
          <button type="submit" class="btn" disabled={!editable} data-testid="capacity-review">{t('Review')}</button>
          {p.source === 'saved' && (
            <button type="button" class="btn" disabled={!editable} data-testid="capacity-reset"
                    onClick={() => { setError(null); setSaved(null); setReview({ kwh: null, summary: t('Stop using the saved value and go back to {v}', { v: fallback(p) }) }); }}>
              {t('Use default')}
            </button>
          )}
        </div>
      </form>
      {error && <div class="field-error" role="alert" data-testid="capacity-error">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="capacity-saved">{saved}</div>}

      {review && (
        <div class="confirm tariff-confirm" role="dialog" data-testid="capacity-confirm">
          <div class="confirm-title">{t('Save the battery capacity?')}</div>
          <div class="confirm-summary">{review.summary}</div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-primary" onClick={apply} disabled={busy} data-testid="capacity-apply">{busy ? t('Saving…') : t('Apply')}</button>
            <button type="button" class="btn" onClick={() => setReview(null)} disabled={busy}>{t('Cancel')}</button>
          </div>
        </div>
      )}
    </Card>
  );
}
