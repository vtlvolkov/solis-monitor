import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { t } from '../lib/i18n';
import { changed, formOf, INTERVALS, parseMqtt, statusLine, type MqttForm, type MqttPayload } from '../lib/mqtt';
import { useSite } from '../lib/site';
import { Card, Skeleton } from './common';

const REFRESH_MS = 5000;

/**
 * Where the poller sends its readings over MQTT (Home Assistant, Node-RED, a relay). Publishing only: nothing that
 * arrives over MQTT reaches the inverter. The settings are kept by the poller, which follows a change at once.
 */
export function MqttCard() {
  const site = useSite();
  const [p, setP] = useState<MqttPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<MqttForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'save' | 'test' | 'off' | null>(null);
  const [note, setNote] = useState<{ text: string; tone: 'ok' | 'bad' } | null>(null);
  const [showMsg, setShowMsg] = useState(false);

  const load = useCallback(async (first: boolean) => {
    try {
      const data = await getJson<MqttPayload>('/api/settings/mqtt');
      setP(data);
      setLoadError(null);
      if (first) setForm(formOf(data, site));
    } catch (e) {
      if (first) setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, [site]);
  useEffect(() => { load(true); }, [load]);
  useEffect(() => {                                    // the connection status follows the poller
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  if (!p || !form) {
    return <Card title="MQTT · Home Assistant">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={220} />}</Card>;
  }

  const editable = p.web.can_change && !busy;
  const edit = (patch: Partial<MqttForm>) => { setForm((f) => (f ? { ...f, ...patch } : f)); setError(null); setNote(null); };
  const line = statusLine(p);

  const send = async (kind: 'save' | 'test' | 'off') => {
    let body: unknown = { enabled: false };
    if (kind !== 'off') {
      const r = parseMqtt(form);
      if (!r.ok) return setError(r.error);
      body = { ...r.value, enabled: true };
    }
    setBusy(kind); setError(null); setNote(null);
    try {
      if (kind === 'test') {
        const r = await postJson<{ ok: boolean; error: string | null; ms: number | null }>('/api/settings/mqtt/test', body);
        setNote(r.ok ? { text: t(p.enabled ? 'The broker answered in {ms} ms. Save to use these settings.' : 'The broker answered in {ms} ms. Turn on to start sending.', { ms: r.ms ?? '?' }), tone: 'ok' }
                     : { text: t('Could not connect: {error}', { error: r.error ? t(r.error) : '?' }), tone: 'bad' });
      } else {
        const answer = await postJson<MqttPayload>('/api/settings/mqtt', body);
        setP(answer);
        setForm(formOf(answer, site));
        setNote({ text: kind === 'off' ? t('MQTT is off. Nothing is sent.') : t('Saved. The poller connects to {broker} within a few seconds.', { broker: `${answer.host}:${answer.port}` }), tone: 'ok' });
      }
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="MQTT · Home Assistant" class="mqtt-card">
      <div class={`update-result tone-${line.tone}`} role="status" data-testid="mqtt-status">{line.text}</div>
      <div class="setting-hint">
        {t('Sends the readings to your MQTT broker, for Home Assistant, Node-RED or a smart relay. Only sends: nothing that arrives over MQTT can change the inverter.')}
      </div>
      {!p.web.can_change && <div class="setting-hint">{p.web.reason ? t(p.web.reason) : ''}</div>}

      <form class="inv-form mqtt-form" onSubmit={(e) => { e.preventDefault(); send('save'); }} data-testid="mqtt-form">
        <label class="mqtt-host"><span>{t('Broker')}</span>
          <input type="text" value={form.host} disabled={!editable} placeholder="192.168.0.20" autocomplete="off" spellcheck={false} data-testid="mqtt-host"
                 onInput={(e) => edit({ host: (e.target as HTMLInputElement).value })} /></label>
        <label class="mqtt-port"><span>{t('Port')}</span>
          <input type="text" inputMode="numeric" value={form.port} disabled={!editable} data-testid="mqtt-port"
                 onInput={(e) => edit({ port: (e.target as HTMLInputElement).value })} /></label>
        <label class="mqtt-user"><span>{t('Username')}</span>
          <input type="text" value={form.username} disabled={!editable} autocomplete="off" spellcheck={false} placeholder={t('optional')} data-testid="mqtt-user"
                 onInput={(e) => edit({ username: (e.target as HTMLInputElement).value })} /></label>
        <label class="mqtt-pass"><span>{t('Password')}</span>
          <input type="password" value={form.password} disabled={!editable} autocomplete="new-password" data-testid="mqtt-password"
                 placeholder={p.password_set ? t('saved · type to change') : t('optional')}
                 onInput={(e) => edit({ password: (e.target as HTMLInputElement).value })} /></label>
        <label class="mqtt-prefix"><span>{t('Topic prefix')}</span>
          <input type="text" value={form.prefix} disabled={!editable} autocomplete="off" spellcheck={false} data-testid="mqtt-prefix"
                 onInput={(e) => edit({ prefix: (e.target as HTMLInputElement).value })} /></label>
        <label class="mqtt-interval"><span>{t('Send every')}</span>
          <select value={form.interval_s} disabled={!editable} data-testid="mqtt-interval"
                  onChange={(e) => edit({ interval_s: Number((e.target as HTMLSelectElement).value) })}>
            {INTERVALS.map((s) => <option key={s} value={s}>{t('{n} s', { n: s })}</option>)}
          </select></label>
        <div class="mqtt-checks">
          <label class="check"><input type="checkbox" checked={form.discovery} disabled={!editable} data-testid="mqtt-discovery"
                                      onChange={(e) => edit({ discovery: (e.target as HTMLInputElement).checked })} />
            <span>{t('Home Assistant discovery')}<span class="setting-hint">{t('The inverter appears in Home Assistant as a device with its sensors, with nothing to set up there.')}</span></span></label>
          <label class="check"><input type="checkbox" checked={form.tls} disabled={!editable} data-testid="mqtt-tls"
                                      onChange={(e) => edit({ tls: (e.target as HTMLInputElement).checked, port: form.port === '1883' && (e.target as HTMLInputElement).checked ? '8883' : form.port === '8883' && !(e.target as HTMLInputElement).checked ? '1883' : form.port })} />
            <span>{t('Encrypted (TLS)')}<span class="setting-hint">{t('Usually port 8883. Leave it off for a broker on your home network.')}</span></span></label>
        </div>
        <div class="update-actions inv-actions">
          <button type="submit" class="btn btn-primary" disabled={!editable || !changed(form, p, site)} data-testid="mqtt-save">
            {busy === 'save' ? t('Saving…') : p.enabled ? t('Save') : t('Turn on')}</button>
          <button type="button" class="btn" disabled={!editable} onClick={() => send('test')} data-testid="mqtt-test">
            {busy === 'test' ? t('Testing…') : t('Test connection')}</button>
          {p.enabled && <button type="button" class="btn" disabled={!editable} onClick={() => send('off')} data-testid="mqtt-off">{t('Turn off')}</button>}
        </div>
      </form>

      {error && <div class="field-error" role="alert" data-testid="mqtt-error">{error}</div>}
      {note && <div class={note.tone === 'ok' ? 'tariff-saved' : 'field-error'} role="status" data-testid="mqtt-note">{note.text}</div>}

      {p.enabled && (
        <div class="mqtt-topics">
          <h3 class="tou-h">{t('Topics')}</h3>
          <dl class="kv kv-compact">
            <div><dt>{t('Readings, every {n} s', { n: p.interval_s })}</dt><dd><code>{p.topics.state}</code></dd></div>
            <div><dt>{t('Outages and alarms, when they start and end')}</dt><dd><code>{p.topics.event}</code></dd></div>
            <div><dt>{t('Online / offline')}</dt><dd><code>{p.topics.availability}</code></dd></div>
            {p.topics.discovery && <div><dt>{t('Home Assistant discovery')}</dt><dd><code>{p.topics.discovery}</code></dd></div>}
          </dl>
          {p.last_message && (
            <>
              <button type="button" class="link" onClick={() => setShowMsg((v) => !v)} aria-expanded={showMsg} data-testid="mqtt-show-message">
                {showMsg ? t('Hide the last message') : t('Show the last message')}</button>
              {showMsg && <pre class="mqtt-message" data-testid="mqtt-message">{JSON.stringify(p.last_message, null, 2)}</pre>}
            </>
          )}
        </div>
      )}
    </Card>
  );
}
