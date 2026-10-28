import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { t } from '../lib/i18n';
import { connectionLine, guessNetwork, networkFrom, parseAddress, type InverterPayload, type ScanResult } from '../lib/inverter';
import { Card, Skeleton } from './common';

const REFRESH_MS = 5000;

type Form = { host: string; port: string; unit: string };
const formOf = (p: InverterPayload): Form => ({ host: p.host, port: String(p.port), unit: String(p.unit) });

/**
 * Where the inverter's data logger is, and a button that finds it. The address is the dashboard's own setting (kept
 * by the poller, which follows a change within seconds without a restart); nothing is written to the inverter.
 */
export function InverterCard() {
  const [p, setP] = useState<InverterPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<Form>({ host: '', port: '502', unit: '1' });
  const [network, setNetwork] = useState('');
  const [scan, setScan] = useState<'idle' | 'running' | ScanResult>('idle');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async (first: boolean) => {
    try {
      const data = await getJson<InverterPayload>('/api/settings/inverter');
      setP(data);
      setLoadError(null);
      if (first) {
        setForm(formOf(data));
        setNetwork(guessNetwork(data.scan_hint, location.hostname));
      }
    } catch (e) {
      if (first) setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(true); }, [load]);
  useEffect(() => {                                    // the connection status follows the poller
    const id = setInterval(() => load(false), REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  if (!p) {
    return <Card title="Inverter connection">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={200} />}</Card>;
  }

  const editable = p.web.can_change && !busy && scan !== 'running';
  const edit = (patch: Partial<Form>) => { setForm({ ...form, ...patch }); setError(null); setSaved(null); };
  const line = connectionLine(p);
  const unchanged = form.host.trim().toLowerCase() === p.host && form.port === String(p.port) && form.unit === String(p.unit);

  const save = async (body: { host: string | null; port: number; unit: number }) => {
    setBusy(true); setError(null); setSaved(null);
    try {
      const answer = await postJson<InverterPayload>('/api/settings/inverter', body);
      setP(answer);
      setForm(formOf(answer));
      setScan('idle');
      setSaved(t('Saved. The poller connects to {host} within a few seconds.', { host: answer.host || '--' }));
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally {
      setBusy(false);
    }
  };

  const onSave = (e: Event) => {
    e.preventDefault();
    const r = parseAddress(form.host, form.port, form.unit);
    if (!r.ok) return setError(r.error);
    save(r.value);
  };

  const onScan = async (e: Event) => {
    e.preventDefault();
    const net = networkFrom(network);
    if (!net) return setError(t('Enter a home network to scan, like 192.168.0'));
    setError(null); setSaved(null); setScan('running');
    try {
      setScan(await postJson<ScanResult>('/api/settings/inverter/scan', { network: net }));
    } catch (err) {
      setScan('idle');
      setError(t(err instanceof ApiError ? err.message : 'Request failed'));
    }
  };

  return (
    <Card title="Inverter connection" class="inverter-card">
      <div class={`update-result tone-${line.tone}`} role="status" data-testid="inverter-status">{line.text}</div>
      <div class="setting-hint">
        {t("The address of the inverter's data logger (the WiFi or Ethernet stick). It accepts only one connection at a time, so stop anything else that reads it.")}
      </div>
      {!p.web.can_change && <div class="setting-hint">{p.web.reason ? t(p.web.reason) : ''}</div>}

      <form class="inv-form" onSubmit={onSave} data-testid="inverter-form">
        <label class="inv-host"><span>{t('Address')}</span>
          <input type="text" value={form.host} disabled={!editable} placeholder="192.168.0.105" autocomplete="off" spellcheck={false} data-testid="inverter-host"
                 onInput={(e) => edit({ host: (e.target as HTMLInputElement).value })} /></label>
        <label><span>{t('Port')}</span>
          <input type="text" inputMode="numeric" value={form.port} disabled={!editable} data-testid="inverter-port"
                 onInput={(e) => edit({ port: (e.target as HTMLInputElement).value })} /></label>
        <label><span>{t('Unit')}</span>
          <input type="text" inputMode="numeric" value={form.unit} disabled={!editable} data-testid="inverter-unit"
                 onInput={(e) => edit({ unit: (e.target as HTMLInputElement).value })} /></label>
        <div class="update-actions inv-actions">
          <button type="submit" class="btn btn-primary" disabled={!editable || unchanged} data-testid="inverter-save">{busy ? t('Saving…') : t('Save')}</button>
          {p.saved && p.env_host && (
            <button type="button" class="btn" disabled={!editable} data-testid="inverter-default"
                    onClick={() => save({ host: null, port: 502, unit: 1 })}>{t('Use {host} again', { host: p.env_host })}</button>
          )}
        </div>
      </form>

      <h3 class="tou-h">{t('Find the logger on your network')}</h3>
      <form class="inv-scan" onSubmit={onScan} data-testid="inverter-scan-form">
        <label><span>{t('Network')}</span>
          <input type="text" value={network} disabled={!editable} placeholder="192.168.0" autocomplete="off" spellcheck={false} data-testid="inverter-network"
                 onInput={(e) => { setNetwork((e.target as HTMLInputElement).value); setError(null); }} /></label>
        <button type="submit" class="btn" disabled={!editable} data-testid="inverter-scan">{scan === 'running' ? t('Searching…') : t('Find inverter')}</button>
      </form>
      <div class="setting-hint">
        {t('Searching briefly connects to every device on that network that has Modbus port 502 open, and pauses readings for about ten seconds.')}
      </div>

      {scan !== 'idle' && scan !== 'running' && (
        scan.found.length === 0 ? (
          <div class="setting-hint" data-testid="inverter-none">
            {t('Nothing answered on {network}. Check that the logger is powered and on this network, and that nothing else is connected to it.', { network: scan.network })}
          </div>
        ) : (
          <ul class="inv-found" data-testid="inverter-found">
            {scan.found.map((host) => (
              <li key={host}>
                <strong>{host}</strong>
                <button type="button" class="btn btn-small" disabled={!editable} data-testid={`inverter-use-${host}`}
                        onClick={() => { setForm({ ...form, host }); setError(null); setSaved(null); }}>{t('Use')}</button>
              </li>
            ))}
          </ul>
        )
      )}

      {error && <div class="field-error" role="alert" data-testid="inverter-error">{error}</div>}
      {saved && <div class="tariff-saved" role="status" data-testid="inverter-saved">{saved}</div>}
    </Card>
  );
}
