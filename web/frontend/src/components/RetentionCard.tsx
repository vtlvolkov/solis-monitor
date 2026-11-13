import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { t } from '../lib/i18n';
import { cutoffDay, dateLabel, deletes, describeDays, describeSource, fallback, parseRetention, type RetentionPayload } from '../lib/retention';
import { Card, Skeleton } from './common';

type Review = { days: number | null; summary: string; warning: string | null };

/**
 * How long the 10-second readings are kept. The poller deletes older ones once a day; the 1-minute averages, the
 * outage history and the settings log are kept for good, so only Grid quality and the 10 s export reach back less far.
 */
export function RetentionCard() {
  const [p, setP] = useState<RetentionPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [value, setValue] = useState('');
  const [keepAll, setKeepAll] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const fill = (d: RetentionPayload) => { setValue(d.days ? String(d.days) : ''); setKeepAll(d.days === 0); };
  const load = useCallback(async () => {
    try {
      const data = await getJson<RetentionPayload>('/api/settings/retention');
      setP(data); fill(data); setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!p) return <Card title="Stored history">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={140} />}</Card>;

  const editable = p.web.can_change && !busy;
  const warningFor = (days: number) => {
    const cut = cutoffDay(days, new Date());
    return deletes(p.days, days) && cut
      ? t('10-second readings from before {date} will be deleted at the next daily clean-up (within a day). This cannot be undone. The 1-minute averages, outages and settings log stay.', { date: dateLabel(cut) })
      : null;
  };
  const onReview = (e: Event) => {
    e.preventDefault();
    const r = parseRetention(value, keepAll, p);
    if (!r.ok) { setReview(null); return setError(r.error); }
    setError(null);
    setReview({ days: r.value, summary: t('10-second readings kept: {from} → {to}', { from: describeDays(p.days), to: describeDays(r.value) }), warning: warningFor(r.value) });
  };
  const apply = async () => {
    if (!review) return;
    setBusy(true);
    try {
      const answer = await postJson<RetentionPayload>('/api/settings/retention', { days: review.days });
      const next = { ...p, ...answer };
      setP(next); fill(next);
      setSaved(t('Saved. 10-second readings are kept for {n}.', { n: describeDays(next.days) }));
      setReview(null);
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally { setBusy(false); }
  };

  return (
    <Card title="Stored history">
      <div class="tariff-now" data-testid="retention-now">
        {t('10-second readings: {n}', { n: describeDays(p.days) })} <span class="muted">({describeSource(p)})</span>
      </div>
      <dl class="kv kv-compact">
        <div><dt>{t('10-second readings since')}</dt><dd data-testid="retention-raw-first">{dateLabel(p.raw_first)}</dd></div>
        <div><dt>{t('1-minute averages since')}</dt><dd data-testid="retention-agg-first">{dateLabel(p.agg_first)}</dd></div>
      </dl>
      <div class="setting-hint">
        {t('Charts, daily energy, cost and exports at 1 minute or coarser use the 1-minute averages, which are kept for good, as are outages and the settings log. Only Grid quality and the 10 s export need the 10-second readings. Older ones are deleted once a day to keep the disk small.')}
      </div>
      {!p.web.can_change && <div class="setting-hint">{p.web.reason ? t(p.web.reason) : ''}</div>}

      <form class="tariff-form" onSubmit={onReview} data-testid="retention-form">
        <label><span>{t('Keep 10-second readings for')} <span class="muted">({t('days, {min}–{max}', { min: p.min_days, max: p.max_days })})</span></span>
          <input type="text" inputMode="numeric" value={value} disabled={!editable || keepAll} data-testid="retention-days"
                 onInput={(e) => { setValue((e.target as HTMLInputElement).value); setReview(null); setError(null); setSaved(null); }} /></label>
        <label class="check retention-all">
          <input type="checkbox" checked={keepAll} disabled={!editable} data-testid="retention-all"
                 onChange={(e) => { setKeepAll((e.target as HTMLInputElement).checked); setReview(null); setError(null); setSaved(null); }} />
          <span>{t('Keep everything')} <span class="muted">{t('(the database grows by about 1–2 GB a year)')}</span></span>
        </label>
        <div class="update-actions">
          <button type="submit" class="btn" disabled={!editable} data-testid="retention-review">{t('Review')}</button>
          {p.source === 'saved' && (
            <button type="button" class="btn" disabled={!editable} data-testid="retention-reset"
                    onClick={() => {
                      const back = p.env_days ?? p.default_days;
                      setError(null); setSaved(null);
                      setReview({ days: null, summary: t('Stop using the saved value and go back to {v}', { v: fallback(p) }), warning: warningFor(back) });
                    }}>
              {t('Use default')}
            </button>
          )}
        </div>
      </form>
      {error && <div class="field-error" role="alert" data-testid="retention-error">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="retention-saved">{saved}</div>}

      {review && (
        <div class={`confirm tariff-confirm ${review.warning ? 'outcome-warn' : ''}`} role="dialog" data-testid="retention-confirm">
          <div class="confirm-title">{t('Change how long readings are kept?')}</div>
          <div class="confirm-summary">{review.summary}</div>
          {review.warning && <div class="setting-hint retention-warning" data-testid="retention-warning">{review.warning}</div>}
          <div class="confirm-actions">
            <button type="button" class={`btn ${review.warning ? 'btn-danger' : 'btn-primary'}`} onClick={apply} disabled={busy} data-testid="retention-apply">{busy ? t('Saving…') : t('Apply')}</button>
            <button type="button" class="btn" onClick={() => setReview(null)} disabled={busy}>{t('Cancel')}</button>
          </div>
        </div>
      )}
    </Card>
  );
}
