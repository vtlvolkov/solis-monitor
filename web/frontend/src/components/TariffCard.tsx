import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { t, unit } from '../lib/i18n';
import {
  dateIn, describeTariff, formatMoment, formFor, parseTariffForm, startOfDay,
  type TariffForm, type TariffPayload, type TariffRequest,
} from '../lib/tariff';
import { Card, Skeleton } from './common';

/**
 * Electricity prices, changeable on the fly. A change starts at the next quarter-hour (or on a chosen
 * date) and only prices consumption from then on: earlier days keep the price they were bought at.
 */
export function TariffCard() {
  const [p, setP] = useState<TariffPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<TariffForm | null>(null);
  const [review, setReview] = useState<TariffRequest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async (resetForm: boolean) => {
    try {
      const data = await getJson<TariffPayload>('/api/settings/tariff');
      setP(data);
      setLoadError(null);
      if (resetForm) setForm(formFor(data.current));
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(true); }, [load]);

  if (!p || !form) {
    return <Card title="Electricity tariff">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={180} />}</Card>;
  }

  const today = dateIn(p.tz);
  const editable = p.can_change && !busy;
  const set = (patch: Partial<TariffForm>) => { setForm({ ...form, ...patch }); setReview(null); setError(null); setSaved(null); };
  const startsAt = (r: TariffRequest) => (r.from ? startOfDay(r.from) : formatMoment(p.next_change_at, p.tz));

  const onReview = (e: Event) => {
    e.preventDefault();
    const r = parseTariffForm(form, today, p.current);
    if (!r.ok) { setReview(null); return setError(r.error); }
    setError(null);
    setReview(r.value);
  };

  const apply = async () => {
    if (!review) return;
    setBusy(true);
    try {
      const answer = await postJson<TariffPayload>('/api/settings/tariff', review);
      setP(answer);
      setForm(formFor(answer.upcoming.length ? answer.upcoming[answer.upcoming.length - 1] : answer.current));
      setSaved(t('Saved. The new prices apply from {time}.', { time: formatMoment(answer.effective_from ?? answer.next_change_at, answer.tz) }));
      setReview(null);
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally {
      setBusy(false);
    }
  };

  const pending = new Set(p.upcoming.map((v) => v.from));
  const changes = p.versions.filter((v) => v.from !== null && !pending.has(v.from)).slice(-6).reverse();   // only those already in force
  const unitName = `${p.currency}/${unit('kWh')}`;

  return (
    <Card title="Electricity tariff" class="tariff-card">
      <div class="tariff-now" data-testid="tariff-now">
        {p.current ? describeTariff(p.current, p.currency) : t('No tariff set yet. The first one you save also applies to all earlier readings.')}
      </div>
      {!p.can_change && <div class="setting-hint">{p.reason ? t(p.reason) : ''}</div>}

      <form class="tariff-form" onSubmit={onReview} data-testid="tariff-form">
        <label><span>{t('Day price')} <span class="muted">{unitName}</span></span>
          <input type="text" inputMode="decimal" value={form.day} disabled={!editable} data-testid="tariff-day"
                 onInput={(e) => set({ day: (e.target as HTMLInputElement).value })} /></label>
        <label><span>{t('Night price')} <span class="muted">{unitName}</span></span>
          <input type="text" inputMode="decimal" value={form.night} disabled={!editable} placeholder={t('empty = one price all day')} data-testid="tariff-night"
                 onInput={(e) => set({ night: (e.target as HTMLInputElement).value })} /></label>
        <label>{t('Night starts')}
          <input type="time" value={form.start} disabled={!editable || form.night.trim() === ''} data-testid="tariff-start"
                 onInput={(e) => set({ start: (e.target as HTMLInputElement).value })} /></label>
        <label>{t('Night ends')}
          <input type="time" value={form.end} disabled={!editable || form.night.trim() === ''} data-testid="tariff-end"
                 onInput={(e) => set({ end: (e.target as HTMLInputElement).value })} /></label>
        <label class="tariff-when">{t('Applies from')}
          <span class={`tariff-when-row ${form.when === 'date' ? 'with-date' : ''}`}>
            <select value={form.when} disabled={!editable} data-testid="tariff-when"
                    onChange={(e) => set({ when: (e.target as HTMLSelectElement).value as 'now' | 'date', date: form.date || today })}>
              <option value="now">{t('Now (next quarter-hour)')}</option>
              <option value="date">{t('A date')}</option>
            </select>
            {form.when === 'date' && (
              <input type="date" value={form.date} min={today} disabled={!editable} data-testid="tariff-date"
                     onInput={(e) => set({ date: (e.target as HTMLInputElement).value })} />
            )}
          </span>
        </label>
        <button type="submit" class="btn" disabled={!editable} data-testid="tariff-review">{t('Review')}</button>
      </form>
      {error && <div class="field-error" role="alert" data-testid="tariff-error">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="tariff-saved">{saved}</div>}

      {review && (
        <div class="confirm tariff-confirm" role="dialog" data-testid="tariff-confirm">
          <div class="confirm-title">{t('Save these prices?')}</div>
          <div class="confirm-summary">
            {describeTariff({ day: review.day, night: review.night, night_start: review.night_start, night_end: review.night_end }, p.currency)}
          </div>
          <div class="setting-hint">{t('Applies from {time}. Consumption and costs before then are not changed.', { time: startsAt(review) })}</div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-primary" onClick={apply} disabled={busy} data-testid="tariff-apply">{busy ? t('Saving…') : t('Apply')}</button>
            <button type="button" class="btn" onClick={() => setReview(null)} disabled={busy}>{t('Cancel')}</button>
          </div>
        </div>
      )}

      {p.upcoming.length > 0 && (
        <div class="tariff-list" data-testid="tariff-upcoming">
          <h3 class="tou-h">{t('Scheduled')}</h3>
          <ul>{p.upcoming.map((v) => <li key={v.from}><time>{formatMoment(v.from!, p.tz)}</time> {describeTariff(v, p.currency)}</li>)}</ul>
          <div class="setting-hint">{t('Saving new prices replaces anything still scheduled that starts later.')}</div>
        </div>
      )}
      {changes.length > 0 && (
        <div class="tariff-list" data-testid="tariff-history">
          <h3 class="tou-h">{t('Price changes')}</h3>
          <ul>{changes.map((v) => <li key={v.from}><time>{formatMoment(v.from!, p.tz)}</time> {describeTariff(v, p.currency)}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}
