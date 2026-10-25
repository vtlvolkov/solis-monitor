import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { locale, t } from '../lib/i18n';
import { describeStatus, progressOf, shouldReload, type UpdatePayload } from '../lib/update';
import { Card, Skeleton } from './common';

const POLL_MS = 3000;
const PICKUP_TIMEOUT_MS = 90_000;      // the host should start within seconds of the request

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(locale(), { dateStyle: 'short', timeStyle: 'short' }) : '';

/**
 * Software version, "Check for updates" and "Update". The dashboard cannot run Docker itself: Update only asks
 * the server's updater (a small unit on the host) and then follows its progress while this page restarts.
 */
export function UpdateCard() {
  const [u, setU] = useState<UpdatePayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<'check' | 'start' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [following, setFollowing] = useState(false);     // asked for an update, or one is running: watch it
  const [offline, setOffline] = useState(false);         // the dashboard itself is restarting
  const asked = useRef<{ version: string | null; startedBefore: string | null; at: number } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await getJson<UpdatePayload>('/api/settings/update');
      setU(next);
      setLoadError(null);
      if (next.busy) setFollowing(true);
      // Arrived from the header badge: show the confirmation straight away (once), then forget the hint.
      const params = new URLSearchParams(location.search);
      if (params.get('update') === '1') {
        params.delete('update');
        history.replaceState(null, '', params.toString() ? `?${params}` : location.pathname);
        if (next.latest?.available && next.can_update && !next.busy) {
          setConfirm(true);
          setTimeout(() => document.querySelector('.update-card')?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 150);
        }
      }
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!following) return;
    const timer = setInterval(async () => {
      try {
        const next = await getJson<UpdatePayload>('/api/settings/update');
        setU(next);
        setOffline(false);
        const a = asked.current;
        if (a && shouldReload(a.version, next)) {
          setTimeout(() => location.reload(), 1500);       // the new version brings a new page
        } else if (!next.busy && (!a || next.status?.started !== a.startedBefore)) {
          setFollowing(false);                             // finished (ok, rolled back or failed): the result stays on screen
        } else if (a && !next.busy && Date.now() - a.at > PICKUP_TIMEOUT_MS) {
          setFollowing(false);
          setError('The server did not pick up the request. On the server run: solis-monitor dashboard-updates on');
        }
      } catch {
        setOffline(true);                                  // expected while the web container is replaced
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [following]);

  if (!u) {
    return <Card title="Software">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={90} />}</Card>;
  }

  const fail = (e: unknown, fallback: string) => setError(t(e instanceof ApiError ? e.message : fallback));

  const check = async () => {
    setBusy('check'); setError(null); setConfirm(false);
    try { setU(await postJson<UpdatePayload>('/api/settings/update/check', {})); } catch (e) { fail(e, 'Request failed'); } finally { setBusy(null); }
  };

  const start = async () => {
    setBusy('start'); setError(null);
    asked.current = { version: u.version, startedBefore: u.status?.started ?? null, at: Date.now() };
    try {
      setU(await postJson<UpdatePayload>('/api/settings/update', {}));
      setConfirm(false);
      setFollowing(true);
    } catch (e) { asked.current = null; fail(e, 'Change failed'); } finally { setBusy(null); }
  };

  const latest = u.latest;
  const inProgress = following || u.busy;
  const result = u.status && (u.status.state !== 'ok' || !latest?.available) ? describeStatus(u.status) : null;

  return (
    <Card title="Software" class="update-card">
      <dl class="kv">
        <div><dt>{t('Version')}</dt><dd data-testid="update-version">{u.version ?? t('built from source')}</dd></div>
        {latest && <div><dt>{t('Latest release')}</dt><dd data-testid="update-latest">{latest.tag}</dd></div>}
      </dl>

      {u.version && (
        <div class="setting-edit update-actions">
          <button type="button" class="btn" onClick={check} disabled={busy !== null || inProgress} data-testid="update-check">
            {busy === 'check' ? t('Checking…') : t('Check for updates')}
          </button>
          {latest?.available && !confirm && !inProgress && (
            <button type="button" class="btn btn-primary" onClick={() => setConfirm(true)} disabled={!u.can_update || busy !== null} data-testid="update-now">
              {t('Update now')}
            </button>
          )}
        </div>
      )}

      {latest && !inProgress && (
        latest.available ? (
          <div class="update-line" data-testid="update-available">
            <strong>{t('{version} is available', { version: latest.tag })}</strong>
            {latest.published && <span class="muted"> · {t('Published {date}', { date: when(latest.published) })}</span>}
            {latest.url && <> · <a href={latest.url} target="_blank" rel="noreferrer noopener">{t("What's new")}</a></>}
          </div>
        ) : (
          <div class="setting-hint" data-testid="update-current">{t('You have the newest version. Checked {time}.', { time: when(latest.checked_at) })}</div>
        )
      )}

      {confirm && latest && (
        <div class="confirm" role="dialog" data-testid="update-confirm">
          <div class="confirm-title">{t('Update to {version}?', { version: latest.tag })}</div>
          <div class="setting-hint">{t('The dashboard restarts and is unavailable for a few minutes, longer on a Raspberry Pi. Readings pause briefly. If the new version does not start, the old one is restored automatically.')}</div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-primary" onClick={start} disabled={busy !== null} data-testid="update-apply">
              {busy === 'start' ? t('Asking the server…') : t('Update')}
            </button>
            <button type="button" class="btn" onClick={() => setConfirm(false)} disabled={busy !== null}>{t('Cancel')}</button>
          </div>
        </div>
      )}

      {inProgress && (() => {
        const p = progressOf(u.status);
        return (
          <div class="update-line" role="status" data-testid="update-progress">
            <div class="progress-head">
              <strong>{u.status?.state === 'running' ? t('Updating to {version}…', { version: u.status.to ?? '' }) : p.label}</strong>
              {p.percent !== null && <span class="muted" data-testid="update-percent">{p.percent}%</span>}
            </div>
            <div class={`progress ${p.percent === null ? 'indeterminate' : ''}`} role="progressbar" aria-label={t('Progress')}
                 aria-valuemin={0} aria-valuemax={100} aria-valuenow={p.percent ?? undefined} data-testid="update-bar">
              <div class="progress-fill" style={p.percent === null ? undefined : { width: `${p.percent}%` }} />
            </div>
            {u.status?.state === 'running' && <div class="setting-hint" data-testid="update-step">{p.label}</div>}
            {offline && <div class="setting-hint">{t('The dashboard is restarting. This page reconnects by itself.')}</div>}
          </div>
        );
      })()}
      {!inProgress && result && (
        <div class={`update-result tone-${result.tone}`} role="status" data-testid="update-result">{result.text}</div>
      )}

      {error && <div class="field-error" role="alert" data-testid="update-error">{error}</div>}
      {!u.can_update && u.reason && u.version && <div class="setting-hint">{t(u.reason)}</div>}
      {!u.version && <div class="setting-hint">{t(u.reason ?? 'This copy was built from source, so it cannot update itself. Update it with git.')}</div>}
    </Card>
  );
}
