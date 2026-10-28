import { useCallback, useEffect, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { locale, t } from '../lib/i18n';
import { describeLogin, parsePasswords, type LoginPayload } from '../lib/login';
import { Card, Skeleton } from './common';

const when = (iso: string) => new Date(iso).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' });

/**
 * The dashboard password. Changing it needs the current one again; the new one is kept on the server as a hash and
 * wins over the original until you go back to it. The browser asks for the new password afterwards.
 */
export function LoginCard() {
  const [p, setP] = useState<LoginPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState({ current: '', next: '', again: '' });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setP(await getJson<LoginPayload>('/api/settings/login'));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!p) return <Card title="Dashboard login">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={160} />}</Card>;

  const editable = p.web.can_change && !busy && !done;
  const edit = (patch: Partial<typeof form>) => { setForm((f) => ({ ...f, ...patch })); setError(null); };      // functional: autofill sets several fields in one tick

  const send = async (body: { current: string; new: string | null }, message: string) => {
    setBusy(true); setError(null);
    try {
      setP(await postJson<LoginPayload>('/api/settings/login', body));
      setForm({ current: '', next: '', again: '' });
      setDone(message);
      setTimeout(() => location.reload(), 2500);          // the browser still holds the old password: reloading makes it ask for the new one
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Change failed'));
    } finally {
      setBusy(false);
    }
  };

  const onSave = (e: Event) => {
    e.preventDefault();
    const r = parsePasswords(form.current, form.next, form.again, p.min_length, p.max_length);
    if (!r.ok) return setError(r.error);
    send({ current: r.value.current, new: r.value.next }, t('Password changed. Your browser will now ask for the new one.'));
  };

  const onOriginal = () => {
    if (!form.current) return setError(t('Enter your current password.'));
    send({ current: form.current, new: null }, t('Back to the original password. Your browser will now ask for it.'));
  };

  return (
    <Card title="Dashboard login" class="login-card">
      <div class="tariff-now" data-testid="login-now">{describeLogin(p, when)}</div>
      {!p.web.can_change && <div class="setting-hint">{p.web.reason ? t(p.web.reason) : ''}</div>}

      <form class="inv-form login-form" onSubmit={onSave} data-testid="login-form">
        <label class="inv-host"><span>{t('Current password')}</span>
          <input type="password" autocomplete="current-password" value={form.current} disabled={!editable} data-testid="login-current"
                 onInput={(e) => edit({ current: (e.target as HTMLInputElement).value })} /></label>
        <label class="inv-host"><span>{t('New password')}</span>
          <input type="password" autocomplete="new-password" value={form.next} disabled={!editable} data-testid="login-new"
                 onInput={(e) => edit({ next: (e.target as HTMLInputElement).value })} /></label>
        <label class="inv-host"><span>{t('New password again')}</span>
          <input type="password" autocomplete="new-password" value={form.again} disabled={!editable} data-testid="login-again"
                 onInput={(e) => edit({ again: (e.target as HTMLInputElement).value })} /></label>
        <div class="update-actions inv-actions">
          <button type="submit" class="btn btn-primary" disabled={!editable} data-testid="login-save">{busy ? t('Saving…') : t('Change password')}</button>
          {p.source === 'saved' && (
            <button type="button" class="btn" disabled={!editable} data-testid="login-original" onClick={onOriginal}>{t('Use the original password again')}</button>
          )}
        </div>
      </form>

      <div class="setting-hint">
        {t('{min} to {max} characters. Anyone with this password can change settings, so keep it to yourself. Forgot it? On the server run: solis-monitor reset-login', { min: p.min_length, max: p.max_length })}
      </div>
      {p.platform && <div class="setting-hint">{t('{platform} keeps showing the original password. It stops working once you change it here.', { platform: p.platform })}</div>}

      {error && <div class="field-error" role="alert" data-testid="login-error">{error}</div>}
      {done && <div class="tariff-saved" role="status" data-testid="login-done">{done}</div>}
    </Card>
  );
}
