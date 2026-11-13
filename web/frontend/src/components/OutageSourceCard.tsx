import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { locale, t } from '../lib/i18n';
import { SOURCES, isGroup, type OutageAlerts, type OutageConfig } from '../lib/outages';
import { Card, Skeleton } from './common';

type Item = { id: number; value: string };
const time = (iso: string) => new Date(iso).toLocaleString(locale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const label = (key: string) => t(SOURCES.find((s) => s.key === key)?.label ?? key);

/** Suggestions for a text field, asked for after a short pause while typing. */
function useSuggest(url: string | null): Item[] {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    if (!url) { setItems([]); return; }
    let live = true;
    const timer = setTimeout(() => getJson<Item[]>(url).then((x) => live && setItems(x)).catch(() => live && setItems([])), 300);
    return () => { live = false; clearTimeout(timer); };
  }, [url]);
  return items;
}

/**
 * Settings → General: where the planned outage schedule comes from. The address is looked up once (street, house,
 * then the group Yasno gives for it), through the server; only the group is kept and asked about every 15 minutes.
 */
export function OutageSourceCard() {
  const [cfg, setCfg] = useState<OutageConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [source, setSource] = useState('yasno-kyiv');
  const [street, setStreet] = useState('');
  const [pickedStreet, setPickedStreet] = useState<Item | null>(null);
  const [house, setHouse] = useState('');
  const [pickedHouse, setPickedHouse] = useState<Item | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [alerts, setAlerts] = useState<OutageAlerts>({ before: true, short: true, changes: true });
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const houseInput = useRef<HTMLInputElement>(null);
  const [showing, setShowing] = useState<'saving' | 'saved' | null>(null);

  const load = useCallback(async () => {
    try {
      const c = await getJson<OutageConfig>('/api/settings/outage-schedule');
      setCfg(c); setLoadError(null); setAlerts(c.alerts);
      if (c.source) setSource(c.source);
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const q = (s: string) => encodeURIComponent(s.trim());
  const streets = useSuggest(!manual && !pickedStreet && street.trim().length >= 3 ? `/api/settings/outage-schedule/streets?source=${source}&q=${q(street)}` : null);
  const houses = useSuggest(!manual && pickedStreet && !pickedHouse && house.trim() ? `/api/settings/outage-schedule/houses?source=${source}&street=${pickedStreet.id}&q=${q(house)}` : null);

  useEffect(() => {
    if (!pickedStreet || !pickedHouse) return;
    setLookupError(null);
    getJson<{ group: string }>(`/api/settings/outage-schedule/group?source=${source}&street=${pickedStreet.id}&house=${pickedHouse.id}`)
      .then((g) => setGroup(g.group))
      .catch((e) => { setGroup(null); setLookupError(t(e instanceof ApiError ? e.message : 'Request failed')); });
  }, [pickedHouse, pickedStreet, source]);

  if (!cfg) return <Card title="Outage schedule">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={160} />}</Card>;

  const editable = cfg.web.can_change && !busy;
  const resetAddress = () => { setStreet(''); setPickedStreet(null); setHouse(''); setPickedHouse(null); setGroup(null); setLookupError(null); };
  const save = async (off = false) => {
    setBusy(true); setError(null); setSaved(null);
    try {
      const c = await postJson<OutageConfig>('/api/settings/outage-schedule', off ? { source: null, group: null, alerts } : { source, group, alerts });
      setCfg({ ...cfg, ...c }); resetAddress(); setManual(false);
      setSaved(off ? t('The outage schedule is off.') : t('Saved. The schedule of group {g} shows on the Overview in a moment.', { g: c.group ?? '' }));
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally { setBusy(false); }
  };
  // Saved at once, with what is saved now (not with an address or Telegram choices still being edited)
  const toggleCard = async (on: boolean) => {
    setShowing('saving'); setError(null);
    try {
      const c = await postJson<OutageConfig>('/api/settings/outage-schedule', { source: cfg.source, group: cfg.group, alerts: cfg.alerts, overview: on });
      setCfg({ ...cfg, ...c }); setShowing('saved');
    } catch (e) {
      setShowing(null); setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    }
  };
  const alertsChanged = cfg.source && JSON.stringify(alerts) !== JSON.stringify(cfg.alerts);
  const canSave = editable && ((!!group && isGroup(group)) || (!!cfg.source && !!alertsChanged && !group));

  return (
    <Card title="Outage schedule" class="outage-source">
      <div class="setting-hint">
        {t('Planned outages for your address, on the Overview and in Telegram. The schedule comes from Yasno, DTEK’s own service for Kyiv and Dnipro.')}
      </div>
      {cfg.source && cfg.group ? (
        <div class="tariff-now" data-testid="outage-current">
          {t('Group {g}', { g: cfg.group })} <span class="muted">· {label(cfg.source)}{cfg.fetched_at ? ` · ${t('fetched {time}', { time: time(cfg.fetched_at) })}` : ''}</span>
        </div>
      ) : <div class="tariff-now muted" data-testid="outage-current">{t('Off')}</div>}
      {cfg.error && <div class="field-error" role="status">{t(cfg.error)}</div>}
      <label class="check outage-show">
        <input type="checkbox" checked={cfg.overview} disabled={!cfg.web.can_change || showing === 'saving'} data-testid="outage-show"
               onChange={(e) => toggleCard((e.target as HTMLInputElement).checked)} />
        <span>{t('Show the Planned outages card on the Overview')}
          {showing === 'saved' && <span class="muted"> · {cfg.overview ? t('shown') : t('hidden')}</span>}</span>
      </label>
      {!cfg.web.can_change && <div class="setting-hint">{cfg.web.reason ? t(cfg.web.reason) : ''}</div>}

      <form class="tariff-form outage-form" onSubmit={(e) => { e.preventDefault(); if (canSave) save(); }}>
        <label><span>{t('Source')}</span>
          <select value={source} disabled={!editable} onChange={(e) => { setSource((e.target as HTMLSelectElement).value); resetAddress(); }} data-testid="outage-source">
            {cfg.sources.map((s) => <option key={s.key} value={s.key}>{label(s.key)}</option>)}
          </select>
        </label>
        <span class="outage-manual-toggle">
          <button type="button" class="link" onClick={() => { setManual(!manual); resetAddress(); }} data-testid="outage-manual-toggle">
            {manual ? t('Find the group by address') : t('I know my group')}
          </button>
        </span>
        {manual ? (
          <label><span>{t('Group')} <span class="muted">{t('(for example 3.1)')}</span></span>
            <input type="text" inputMode="decimal" value={group ?? ''} disabled={!editable} data-testid="outage-group-input"
                   onInput={(e) => setGroup((e.target as HTMLInputElement).value.trim() || null)} />
          </label>
        ) : (
          <>
            <label class="outage-street"><span>{t('Street')}</span>
              <input type="text" value={pickedStreet ? pickedStreet.value : street} placeholder={t('Start typing, e.g. Хрещатик')} autocomplete="off" disabled={!editable} data-testid="outage-street"
                     onInput={(e) => { resetAddress(); setStreet((e.target as HTMLInputElement).value); }} />
              {streets.length > 0 && !pickedStreet && (
                <ul class="outage-suggest" data-testid="outage-suggest">
                  {streets.map((s) => <li key={s.id}><button type="button" onClick={() => { setPickedStreet(s); setTimeout(() => houseInput.current?.focus(), 0); }}>{s.value}</button></li>)}
                </ul>
              )}
            </label>
            <label class="outage-street"><span>{t('House')}</span>
              <input ref={houseInput} type="text" value={pickedHouse ? pickedHouse.value : house} placeholder={pickedStreet ? t('Number') : '—'} autocomplete="off"
                     disabled={!editable || !pickedStreet} data-testid="outage-house"
                     onInput={(e) => { setPickedHouse(null); setGroup(null); setHouse((e.target as HTMLInputElement).value); }} />
              {houses.length > 0 && !pickedHouse && (
                <ul class="outage-suggest" data-testid="outage-house-suggest">
                  {houses.map((h) => <li key={h.id}><button type="button" onClick={() => setPickedHouse(h)}>{h.value}</button></li>)}
                </ul>
              )}
            </label>
          </>
        )}
      </form>
      {group && !manual && (
        <div class="update-result tone-ok outage-found" data-testid="outage-group">
          {t('Your group: {g}', { g: group })} <span class="muted">· {t('the address itself is not kept, only the group')}</span>
        </div>
      )}
      {manual && group && !isGroup(group) && <div class="field-error">{t('A group looks like 3.1')}</div>}
      {lookupError && <div class="field-error" role="alert" data-testid="outage-lookup-error">{lookupError}</div>}

      <div class="outage-alerts">
        <div class="export-label">{t('Telegram')}</div>
        {([['before', '30 minutes before a planned outage, with the battery level and runtime'],
           ['short', 'When the battery will not last through the next outage'],
           ['changes', 'When tomorrow’s schedule is published or the schedule changes']] as const).map(([k, text]) => (
          <label class="check" key={k}>
            <input type="checkbox" checked={alerts[k]} disabled={!editable} onChange={() => setAlerts({ ...alerts, [k]: !alerts[k] })} data-testid={`outage-alert-${k}`} />
            <span>{t(text)}</span>
          </label>
        ))}
      </div>
      <div class="update-actions">
        <button type="button" class="btn btn-primary" disabled={!canSave} onClick={() => save()} data-testid="outage-save">{busy ? t('Saving…') : t('Save')}</button>
        {cfg.source && <button type="button" class="btn" disabled={!editable} onClick={() => save(true)} data-testid="outage-off">{t('Turn off')}</button>}
      </div>
      {error && <div class="field-error" role="alert">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="outage-saved">{saved}</div>}
      <div class="setting-hint">{t('While this is on, the server asks app.yasno.ua for your group’s schedule every 15 minutes. Nothing else is sent.')}</div>
    </Card>
  );
}
