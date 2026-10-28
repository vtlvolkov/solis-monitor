import type { ComponentChildren } from 'preact';
import { useCallback, useEffect, useState } from 'preact/hooks';
import { Card, Header, Skeleton } from '../components/common';
import { CapacityCard } from '../components/CapacityCard';
import { InverterCard } from '../components/InverterCard';
import { LoginCard } from '../components/LoginCard';
import { TariffCard } from '../components/TariffCard';
import { UpdateCard } from '../components/UpdateCard';
import { goTo } from '../lib/nav';
import { SETTINGS_TABS, tabFrom, type SettingsTab } from '../lib/settingsTabs';
import { ApiError, getJson } from '../lib/api';
import { holdInfo } from '../lib/hold';
import { locale, t, tx, unit } from '../lib/i18n';
import type { LiveState } from '../lib/live';
import {
  blockedReason, clockOffset, describeChange, describeSlot, label, parseAmps, parseSoc, socBounds, validateSlot,
  type ChangeLogEntry, type ChangeResult, type SettingKey, type SettingsPayload, type SlotForm, type SocKey, type TouKind,
  type TouSlot, type TouValue,
} from '../lib/settings';

type Value = boolean | number | TouValue;
type Pending = { key: SettingKey; value: Value; summary: string };
type Propose = (p: Pending) => void;
type Outcome = { kind: 'ok' | 'warn' | 'bad'; text: string };

async function postChange(key: SettingKey, value: Value): Promise<ChangeResult> {
  let res: Response;
  try {
    res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ setting: key, value }),
    });
  } catch {
    throw new ApiError('Dashboard server unreachable', 0);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.detail ?? res.statusText, res.status);
  return body;
}

function outcomeOf(r: ChangeResult): Outcome {
  if (r.no_change) return { kind: 'ok', text: t('Already set; nothing was written.') };
  if (r.verified) return { kind: 'ok', text: t('Applied and verified: {what}', { what: r.applied ?? '' }) };
  return { kind: 'warn', text: t(r.warning ?? 'The inverter did not keep the value.') };
}

function CurrentRow({ s, k, wait, onPropose }: {
  s: SettingsPayload; k: 'max_charge_current_a' | 'max_discharge_current_a'; wait: number;
  onPropose: (p: Pending) => void;
}) {
  const current = s[k];
  const limit = s.control.limits[k] ?? 0;
  const raw = k === 'max_charge_current_a' ? s.max_charge_current_raw : s.max_discharge_current_raw;
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const blocked = blockedReason(s, k, wait);
  useEffect(() => { setText(''); setError(null); }, [current]);   // clear after a change lands

  const review = (e: Event) => {
    e.preventDefault();
    const p = parseAmps(text, limit, current);
    if (!p.ok) return setError(p.error);
    setError(null);
    onPropose({ key: k, value: p.value, summary: describeChange(k, current, p.value) });
  };

  return (
    <div class="setting" data-testid={`setting-${k}`}>
      <div class="setting-main">
        <div>
          <div class="setting-label">{t(label(k))}</div>
          <div class="setting-hint">
            {limit > 0 ? t('Allowed 0–{n} A', { n: limit }) : t('No limit set')} · {t('raw')} {raw}
            {k === 'max_discharge_current_a' && ` · ${t('0 A = battery does not power loads')}`}
          </div>
        </div>
        <div class="setting-value">{current.toFixed(1)} {unit('A')}</div>
      </div>
      <form class="setting-edit" onSubmit={review}>
        <input type="text" inputMode="decimal" placeholder={t('New value, A')} value={text} disabled={!!blocked}
               aria-label={t('New {name} in amps', { name: label(k).toLowerCase() })}
               onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        <button type="submit" class="btn" disabled={!!blocked || !text.trim()}>{t('Review')}</button>
      </form>
      {error && <div class="field-error">{error}</div>}
    </div>
  );
}

const FLAG_TEXT: Record<'grid_charging' | 'time_of_use_mode' | 'reserve_mode', { hint: string; on: string; off: string }> = {
  grid_charging: { hint: tx('Charge the battery from the grid (43110 bit 5)'), on: tx('Allowed'), off: tx('Forbidden') },
  time_of_use_mode: { hint: tx('Older-firmware switch for timed charging (43110 bit 1). On this inverter the slots below run on their own on/off switches, without it'), on: tx('On'), off: tx('Off') },
  reserve_mode: { hint: tx('Hold the battery at the backup SOC for outages (43110 bit 4)'), on: tx('On'), off: tx('Off') },
};

function FlagRow({ s, k, wait, onPropose, children }: {
  s: SettingsPayload; k: keyof typeof FLAG_TEXT; wait: number; onPropose: Propose; children?: ComponentChildren;
}) {
  const cur = s[k];
  const ft = FLAG_TEXT[k];
  const blocked = blockedReason(s, k, wait);
  return (
    <div class="setting" data-testid={`setting-${k}`}>
      <div class="setting-main">
        <div>
          <div class="setting-label">{t(label(k))}</div>
          <div class="setting-hint">{t(ft.hint)}</div>
        </div>
        <div class={`setting-value ${cur ? 'on' : ''}`}>{t(cur ? ft.on : ft.off)}</div>
      </div>
      <div class="setting-edit">
        <button type="button" class="btn" disabled={!!blocked}
                onClick={() => onPropose({ key: k, value: !cur, summary: describeChange(k, cur, !cur) })}>
          {k === 'grid_charging' ? (cur ? t('Forbid grid charging') : t('Allow grid charging')) : cur ? t('Turn off') : t('Turn on')}
        </button>
      </div>
      {children && <div class="setting-hint">{children}</div>}
    </div>
  );
}

function SocRow({ s, k, wait, onPropose, hint }: {
  s: SettingsPayload; k: SocKey; wait: number; onPropose: Propose; hint: string;
}) {
  const current = s.battery[k];
  const [lo, hi] = socBounds(s.battery, k);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const blocked = blockedReason(s, k, wait);
  useEffect(() => { setText(''); setError(null); }, [current]);
  const review = (e: Event) => {
    e.preventDefault();
    const p = parseSoc(text, lo, hi, current);
    if (!p.ok) return setError(p.error);
    setError(null);
    onPropose({ key: k, value: p.value, summary: describeChange(k, current, p.value) });
  };
  return (
    <div class="setting" data-testid={`setting-${k}`}>
      <div class="setting-main">
        <div>
          <div class="setting-label">{t(label(k))}</div>
          <div class="setting-hint">{t(hint)} · {t('allowed {lo}–{hi}%', { lo, hi })}</div>
        </div>
        <div class="setting-value">{current}%</div>
      </div>
      <form class="setting-edit" onSubmit={review}>
        <input type="text" inputMode="numeric" placeholder={t('New value, %')} value={text} disabled={!!blocked}
               aria-label={t('New {name} in percent', { name: label(k).toLowerCase() })}
               onInput={(e) => setText((e.target as HTMLInputElement).value)} />
        <button type="submit" class="btn" disabled={!!blocked || !text.trim()}>{t('Review')}</button>
      </form>
      {error && <div class="field-error">{error}</div>}
    </div>
  );
}

function SlotRow({ s, kind, slot, wait, onPropose }: {
  s: SettingsPayload; kind: TouKind; slot: TouSlot; wait: number; onPropose: Propose;
}) {
  const limitKey = kind === 'charge' ? 'max_charge_current_a' : 'max_discharge_current_a';
  const limit = s.control.limits[limitKey] ?? 0;
  const blocked = blockedReason(s, 'tou_slot', wait, limitKey);
  const initial = (): SlotForm => ({ enabled: slot.enabled, start: slot.start, end: slot.end,
                                     current: String(slot.current_a), soc: String(slot.soc) });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<SlotForm>(initial);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setForm(initial()); setOpen(false); setError(null); }, [slot]);   // reset after a change lands
  const set = (patch: Partial<SlotForm>) => setForm({ ...form, ...patch });
  const review = (e: Event) => {
    e.preventDefault();
    const r = validateSlot(kind, slot, form, limit, s.battery.overdischarge_soc);
    if (!r.ok) return setError(r.error);
    setError(null);
    onPropose({ key: 'tou_slot', value: r.value, summary: describeSlot(kind, slot, r.value) });
  };
  return (
    <li class={`slot ${slot.enabled ? 'slot-on' : ''}`} data-testid={`slot-${kind}-${slot.slot}`}>
      <div class="slot-main">
        <span class={`slot-state ${slot.enabled ? 'on' : ''}`}>{slot.enabled ? t('On') : t('Off')}</span>
        <span class="slot-time">{slot.start}–{slot.end}</span>
        <span class="slot-meta">{slot.current_a} {unit('A')} · {kind === 'charge' ? t('to') : t('down to')} {slot.soc}%</span>
        <button type="button" class="btn btn-small" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? t('Close') : t('Edit')}
        </button>
      </div>
      {open && (
        <form class="slot-form" onSubmit={review}>
          <label class="toggle"><input type="checkbox" checked={form.enabled}
                 onChange={(e) => set({ enabled: (e.target as HTMLInputElement).checked })} /><span>{t('Enabled')}</span></label>
          <label>{t('From')} <input type="time" value={form.start} onInput={(e) => set({ start: (e.target as HTMLInputElement).value })} /></label>
          <label>{t('To')} <input type="time" value={form.end} onInput={(e) => set({ end: (e.target as HTMLInputElement).value })} /></label>
          <label>{t('Current, A')} <input type="text" inputMode="decimal" value={form.current}
                 onInput={(e) => set({ current: (e.target as HTMLInputElement).value })} /></label>
          <label>{kind === 'charge' ? t('Charge to, %') : t('Stop at, %')} <input type="text" inputMode="numeric" value={form.soc}
                 onInput={(e) => set({ soc: (e.target as HTMLInputElement).value })} /></label>
          <button type="submit" class="btn" disabled={!!blocked}>{t('Review')}</button>
          {blocked && <div class="setting-hint">{blocked}</div>}
          {error && <div class="field-error">{error}</div>}
        </form>
      )}
    </li>
  );
}

/** Explains what the charge slots and reserve mode are doing to the battery right now. */
function HoldCard({ s }: { s: SettingsPayload }) {
  const hold = holdInfo(s);
  const floor = s.battery.overdischarge_soc;
  return (
    <Card title="Battery hold" class="span-2">
      <div class={`now-row ${hold ? 'charge' : 'idle'}`} data-testid="hold-state">
        <span class="now-dot" />
        <div>
          <div class="now-title">{hold ? t('Holding at {soc}%', { soc: hold.slot.soc }) : t('Not held from the grid')}</div>
          <div class="setting-hint">
            {hold
              ? t('Charge slot {n} ({start}–{end}) is on and grid charging is allowed, so the inverter keeps the battery at {soc}% from the grid.',
                  { n: hold.slot.slot, start: hold.slot.start, end: hold.slot.end, soc: hold.slot.soc })
              : t('No charge slot covers most of the day, so the battery is not held from the grid.')}
          </div>
        </div>
      </div>
      {hold ? (
        <>
          <div class="setting-hint">
            {t('When the charge level sags by 1–2% (the BMS and inverter draw about 20–25 W even when idle), it tops the battery up again for 10–15 minutes. This is normal, not a fault, and costs only a few hundred Wh a day.')}
          </div>
          <div class="setting-hint">
            {t('To stop the daytime top-ups: limit the slot to your night-tariff hours (for example 23:00–07:00), turn on Reserve battery mode and set the Backup (reserve) SOC to the charge you want to keep for outages (for example 60%). Change one setting at a time and watch the Overview for a day.')}
          </div>
        </>
      ) : (
        <div class="setting-hint">
          {s.reserve_mode
            ? t('Reserve mode is on: {n}% is kept back for outages.', { n: s.battery.backup_soc })
            : t('Reserve mode is off: the battery can be used down to {n}%.', { n: floor })}
        </div>
      )}
    </Card>
  );
}

function TouCard({ s, wait, onPropose }: { s: SettingsPayload; wait: number; onPropose: Propose }) {
  return (
    <Card title="Time-of-Use schedule" class="span-2">
      <div class="setting-hint tou-note">
        {t('Six charge and six discharge slots (registers 43707–43791). Slots may cross midnight, e.g. 23:00–07:00 for a night tariff. Charge slots charge from the grid up to their SOC; discharge slots discharge down to theirs.')}
        {' '}{t('Each slot has its own on/off switch, which is what the inverter follows (it charged from the grid to 100% in slot 1 with Time-of-Use mode off).')}
      </div>
      <div class="tou-cols">
        {(['charge', 'discharge'] as const).map((kind) => (
          <div key={kind}>
            <h3 class="tou-h">{kind === 'charge' ? t('Charge') : t('Discharge')}</h3>
            <ul class="slots">
              {s.tou[kind].map((slot) => <SlotRow key={slot.slot} s={s} kind={kind} slot={slot} wait={wait} onPropose={onPropose} />)}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}

function ClockCard({ s, wait, onPropose }: { s: SettingsPayload; wait: number; onPropose: Propose }) {
  const off = clockOffset(s.clock, s.control.server_time);
  const blocked = blockedReason(s, 'clock_sync', wait);
  const fmt = (iso: string | null) => (iso ? iso.replace('T', ' ') : '--');
  const drift = off == null ? '' : Math.abs(off) < 5 ? t('in sync') : `${off > 0 ? '+' : '−'}${Math.abs(off) >= 60 ? t('{n} min', { n: Math.round(Math.abs(off) / 60) }) : t('{n} s', { n: Math.abs(off) })}`;
  return (
    <Card title="Inverter clock">
      <dl class="kv">
        <div><dt>{t('Inverter')}</dt><dd>{fmt(s.clock)}</dd></div>
        <div><dt>{t('Server')}</dt><dd>{fmt(s.control.server_time)}</dd></div>
        <div><dt>{t('Difference')}</dt><dd class={off != null && Math.abs(off) >= 60 ? 'warn-text' : ''}>{drift || '--'}</dd></div>
      </dl>
      <div class="setting-edit clock-edit">
        <button type="button" class="btn" disabled={!!blocked} data-testid="clock-sync"
                onClick={() => onPropose({ key: 'clock_sync', value: true,
                                           summary: t('Set the inverter clock to the server time (now {drift})', { drift: drift || t('unknown') }) })}>
          {t('Sync to server time')}
        </button>
      </div>
      <div class="setting-hint">{t('Time-of-Use slots run on the inverter\'s clock.')}</div>
    </Card>
  );
}

const TAB_LABELS: Record<SettingsTab, string> = {
  general: tx('General'), battery: tx('Battery'), schedule: tx('Schedule'), system: tx('System'),
};

export function Settings({ live, now }: { live: LiveState; now: number }) {
  const [s, setS] = useState<SettingsPayload | null>(null);
  const [loadedAt, setLoadedAt] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [signIn, setSignIn] = useState(false);
  const [log, setLog] = useState<ChangeLogEntry[] | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [applying, setApplying] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setS(await getJson<SettingsPayload>('/api/settings'));
      setLoadedAt(Date.now());
      setLoadError(null);
      setSignIn(false);
    } catch (e) {
      const denied = e instanceof ApiError && e.status === 401;   // password prompt cancelled or wrong
      setSignIn(denied);
      setLoadError(denied ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
      if (denied) return;                                          // don't prompt a second time for the log
    } finally {
      setLoading(false);
    }
    getJson<{ changes: ChangeLogEntry[] }>('/api/settings/log?limit=10')
      .then((r) => setLog(r.changes)).catch(() => setLog(null));
  }, []);

  useEffect(() => { load(); }, [load]);

  const wait = s ? Math.max(0, Math.ceil(s.control.next_change_in_s - (now - loadedAt) / 1000)) : 0;

  const apply = async () => {
    if (!pending) return;
    setApplying(true);
    setOutcome(null);
    try {
      setOutcome(outcomeOf(await postChange(pending.key, pending.value)));
    } catch (e) {
      setOutcome({ kind: 'bad', text: t(e instanceof ApiError ? e.message : 'Change failed') });
    } finally {
      setApplying(false);
      setPending(null);
      load();
    }
  };

  const propose = (p: Pending) => { setOutcome(null); setPending(p); };

  const tab = tabFrom(location.search);
  const workCard = (
<Card title="Work mode" aside={
            <button type="button" class="btn btn-small" onClick={load} disabled={loading}>{loading ? t('Reading…') : t('Refresh')}</button>
          }>
            {!s ? <Skeleton h={220} /> : (
              <>
                <FlagRow s={s} k="grid_charging" wait={wait} onPropose={propose}>
                  {t('Forbidding it doesn\'t stop charging from PV. Grid charging also needs a charge slot or mode that asks for it.')}
                </FlagRow>
                <FlagRow s={s} k="time_of_use_mode" wait={wait} onPropose={propose} />
                <FlagRow s={s} k="reserve_mode" wait={wait} onPropose={propose} />
                <div class="setting-hint">
                  {t('Self-use is {state}.', { state: s.storage_flags.self_use ? t('on') : t('off') })}{' '}
                  {t('Off-grid and feed-in priority can only be changed on the inverter or in SolisCloud: switching them by mistake could cut the grid connection.')}
                </div>
                {s.web.can_change && wait > 0 && <div class="setting-hint" data-testid="wait">{t('Next change allowed in {n} s', { n: wait })}</div>}
              </>
            )}
          </Card>
  );
  const limitsCard = (
<Card title="Battery limits">
            {!s ? <Skeleton h={220} /> : (
              <>
                <SocRow s={s} k="overdischarge_soc" wait={wait} onPropose={propose}
                        hint={tx('Normal discharging stops here (keeps a reserve when the grid is up)')} />
                <SocRow s={s} k="backup_soc" wait={wait} onPropose={propose}
                        hint={tx('Target kept in reserve battery mode')} />
                <SocRow s={s} k="max_charge_soc" wait={wait} onPropose={propose}
                        hint={tx('Charging stops here')} />
                <SocRow s={s} k="force_charge_soc" wait={wait} onPropose={propose}
                        hint={tx('Below this the inverter charges the battery from the grid to protect it')} />
                <CurrentRow s={s} k="max_charge_current_a" wait={wait} onPropose={propose} />
                <CurrentRow s={s} k="max_discharge_current_a" wait={wait} onPropose={propose} />
                <dl class="kv kv-tight">
                  <div><dt>{t('Discharge floor on backup output')}</dt><dd>{s.battery.eps_dod}%</dd></div>
                  <div><dt>{t('Off-grid floor')}</dt><dd>{s.battery.offgrid_overdischarge_soc}%</dd></div>
                  <div><dt>{t('Rated capacity (setting)')}</dt><dd>{s.battery.rated_capacity_ah} {unit('Ah')}</dd></div>
                  <div><dt>{t('Float / equalise voltage')}</dt><dd>{s.battery.float_voltage} {unit('V')} / {s.battery.equalize_voltage} {unit('V')}</dd></div>
                </dl>
              </>
            )}
          </Card>
  );
  const logCard = (
<Card title="Recent changes">
            {log == null ? <div class="setting-hint">{t('Change log unavailable.')}</div>
              : log.length === 0 ? <div class="setting-hint">{t('No changes yet.')}</div>
              : (
                <ul class="changelog">
                  {log.map((c) => (
                    <li key={c.time}>
                      <span class={`dot ${c.verified ? 'ok' : 'bad'}`} title={c.verified ? t('verified') : t('not kept by the inverter')} />
                      <span class="changelog-summary">{c.summary ?? c.action}</span>
                      <time>{new Date(c.time).toLocaleString(locale(), { dateStyle: 'short', timeStyle: 'short' })}</time>
                    </li>
                  ))}
                </ul>
              )}
          </Card>
  );
  const storageCard = (
<Card title="Storage mode">
              {!s ? <Skeleton h={120} /> : (
                <>
                  <div class="flags">
                    {Object.entries(s.storage_flags).map(([name, on]) => (
                      <span key={name} class={`flag ${on ? 'on' : ''}`}>{t(name.replace(/_/g, ' '))}</span>
                    ))}
                  </div>
                  <div class="setting-hint">{t('Register 43110 = {n}.', { n: s.storage_switch_raw })}</div>
                </>
              )}
            </Card>
  );

  return (
    <div class="page settings">
      <Header live={live} now={now} title="Settings" />

      {loadError && (
        <div class="banner banner-bad" role="alert" data-testid="settings-error">
          <strong>{t(loadError)}</strong>
          <span>{signIn ? t('The browser asks for the dashboard username and password.') : t('Settings are read from the inverter through the poller.')}</span>
          {signIn && <button type="button" class="btn" onClick={load} disabled={loading}>{t('Sign in')}</button>}
        </div>
      )}
      {s && !s.web.can_change && (
        <div class="banner" role="status" data-testid="settings-readonly"><strong>{t('View only.')}</strong><span>{s.web.reason ? t(s.web.reason) : ''}</span></div>
      )}

      {(pending || outcome) && (
        <div class="sheet">
          {pending ? (
            <div class="confirm" role="dialog" aria-labelledby="confirm-title" data-testid="confirm">
              <div id="confirm-title" class="confirm-title">{t('Apply this change to the inverter?')}</div>
              <div class="confirm-summary">{pending.summary}</div>
              <div class="confirm-actions">
                <button type="button" class="btn btn-primary" onClick={apply} disabled={applying} data-testid="confirm-apply">
                  {applying ? t('Applying… checking read-back') : t('Apply')}
                </button>
                <button type="button" class="btn" onClick={() => setPending(null)} disabled={applying}>{t('Cancel')}</button>
              </div>
            </div>
          ) : outcome && (
            <div class={`confirm outcome-${outcome.kind}`} role="status" data-testid="outcome">
              <div class="confirm-summary">{outcome.text}</div>
              <div class="confirm-actions"><button type="button" class="btn" onClick={() => setOutcome(null)}>{t('OK')}</button></div>
            </div>
          )}
        </div>
      )}

      <nav class="settings-tabs" role="tablist" aria-label={t('Settings sections')}>
        {SETTINGS_TABS.map((k) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} class={tab === k ? 'on' : ''} data-testid={`tab-${k}`}
                  onClick={() => goTo('settings', { tab: k })}>{t(TAB_LABELS[k])}</button>
        ))}
      </nav>

      {tab === 'general' && (
        <>
          <div class="settings-row settings-general">
            <InverterCard />
            {s ? <ClockCard s={s} wait={wait} onPropose={propose} /> : <Card title="Inverter clock"><Skeleton h={120} /></Card>}
          </div>
          <div class="settings-row settings-general">
            <TariffCard />
            {storageCard}
          </div>
        </>
      )}
      {tab === 'battery' && (
        <>
          <div class="settings-row">
            <div class="settings-col">{workCard}{logCard}</div>
            <div class="settings-col">{limitsCard}<CapacityCard /></div>
          </div>
          {s && <HoldCard s={s} />}
        </>
      )}
      {tab === 'schedule' && s && <TouCard s={s} wait={wait} onPropose={propose} />}
      {tab === 'system' && (
        <div class="settings-row">
          <UpdateCard />
          <LoginCard />
        </div>
      )}
    </div>
  );
}
