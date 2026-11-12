import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { describeBackup, downloadUrl, folderError, formatSize, type BackupPayload } from '../lib/backup';
import { locale, t } from '../lib/i18n';
import { Card, Skeleton } from './common';

const POLL_MS = 3000;
const PICKUP_TIMEOUT_MS = 90_000;      // the host should start within seconds of the request

const when = (iso: string) => new Date(iso).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' });

/**
 * Settings → Backup. The dashboard cannot read the database's files or run anything on the server: "Back up now"
 * asks a small helper on the host (the same bridge the updater uses), then follows it; the archives it made are
 * listed from a read-only view of the server's backups folder, for download.
 */
export function BackupCard() {
  const [b, setB] = useState<BackupPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [following, setFollowing] = useState(false);
  const [editing, setEditing] = useState(false);
  const [dir, setDir] = useState('');
  const [keep, setKeep] = useState('5');
  const [formError, setFormError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);     // the archive whose Remove is waiting for a confirmation
  const asked = useRef<{ startedBefore: string | null; at: number } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await getJson<BackupPayload>('/api/settings/backups');
      setB(next);
      setLoadError(null);
      if (next.busy) setFollowing(true);
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 401 ? 'Sign in to open Settings' : e instanceof ApiError ? e.message : 'Could not read settings');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!following) return;
    const timer = setInterval(async () => {
      try {
        const next = await getJson<BackupPayload>('/api/settings/backups');
        setB(next);
        const a = asked.current;
        if (!next.busy && (!a || next.status?.started !== a.startedBefore)) {
          setFollowing(false);                             // finished (ok or failed): the result stays on screen
        } else if (a && !next.busy && Date.now() - a.at > PICKUP_TIMEOUT_MS) {
          setFollowing(false);
          setError(t('The server did not pick up the request. On the server run: solis-monitor dashboard-updates on'));
        }
      } catch { /* a blip; the next tick tries again */ }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [following]);

  if (!b) {
    return <Card title="Backups">{loadError ? <div class="setting-hint">{t(loadError)}</div> : <Skeleton h={120} />}</Card>;
  }
  if (b.managed_by) {                                      // the Umbrel app: no helper on the host, umbrelOS backs the app's data up
    return (
      <Card title="Backups" class="backup-card">
        <p class="setting-hint" data-testid="backup-managed"><strong>{t(b.reason ?? 'Backups are handled by {platform}.', { platform: b.managed_by })}</strong></p>
        <p class="setting-hint">{t("The app's data folder (the history, and the settings saved on this dashboard) is included in the platform's own app backups. Nothing needs to be set up here, and there is nothing to download from this page.")}</p>
      </Card>
    );
  }

  const start = async () => {
    setBusy(true); setError(null);
    asked.current = { startedBefore: b.status?.started ?? null, at: Date.now() };
    try {
      setB(await postJson<BackupPayload>('/api/settings/backups', {}));
      setFollowing(true);
    } catch (e) {
      asked.current = null;
      setError(t(e instanceof ApiError ? e.message : 'Request failed'));
    } finally { setBusy(false); }
  };

  const openFolder = () => {
    setDir(b.folder.dir ?? b.folder.default_dir ?? '');
    setKeep(String(b.folder.keep));
    setFormError(null);
    setEditing(true);
  };
  const saveFolder = async () => {
    const bad = folderError(dir);
    const n = Number(keep);
    if (bad) { setFormError(bad); return; }
    if (!Number.isInteger(n) || n < b.folder.keep_min || n > b.folder.keep_max) {
      setFormError(t('Keep must be a whole number from {min} to {max}.', { min: b.folder.keep_min, max: b.folder.keep_max })); return;
    }
    setBusy(true); setError(null); setFormError(null);
    asked.current = { startedBefore: b.status?.started ?? null, at: Date.now() };
    try {
      setB(await postJson<BackupPayload>('/api/settings/backups/folder', { dir: dir.trim(), keep: n }));
      setEditing(false);
      setFollowing(true);
    } catch (e) {
      asked.current = null;
      setFormError(t(e instanceof ApiError ? e.message : 'Request failed'));
    } finally { setBusy(false); }
  };

  const remove = async (name: string) => {
    setBusy(true); setError(null);
    asked.current = { startedBefore: b.status?.started ?? null, at: Date.now() };
    try {
      setB(await postJson<BackupPayload>('/api/settings/backups/remove', { name }));
      setRemoving(null);
      setFollowing(true);
    } catch (e) {
      asked.current = null;
      setError(t(e instanceof ApiError ? e.message : 'Request failed'));
    } finally { setBusy(false); }
  };

  const inProgress = following || b.busy;
  const result = b.status && !inProgress ? describeBackup(b.status) : null;
  const folder = b.folder.dir ?? b.folder.default_dir;
  const elsewhere = b.backups.some((x) => !x.downloadable);

  return (
    <Card title="Backups" class="backup-card">
      <p class="setting-hint">
        {t('A backup is one file with the whole history, the settings saved on this dashboard and the configuration of the server. It is made on the server; download it and keep it somewhere else than this machine.')}
      </p>
      <p class="setting-hint">
        <strong>{t('A backup contains your passwords and tokens.')}</strong> {t('Keep it as private as the server itself.')}
      </p>

      {!b.managed_by && (
        <div class="setting-edit update-actions">
          <button type="button" class="btn btn-primary" onClick={start} disabled={busy || inProgress || !b.can_backup} data-testid="backup-now">
            {busy ? t('Asking the server…') : inProgress ? t('Backing up…') : t('Back up now')}
          </button>
        </div>
      )}
      {inProgress && (
        <div class="update-line" role="status" data-testid="backup-progress">
          <div class="progress indeterminate" role="progressbar" aria-label={t('Progress')}><div class="progress-fill" /></div>
          <div class="setting-hint">{b.status?.state === 'running' && b.status.message ? t(b.status.message) : t('Waiting for the server to start the backup…')}</div>
        </div>
      )}
      {result && <div class={`update-result tone-${result.tone}`} role="status" data-testid="backup-result">{result.text}</div>}
      {error && <div class="field-error" role="alert" data-testid="backup-error">{error}</div>}
      {!b.can_backup && b.reason && <div class="setting-hint" data-testid="backup-reason">{t(b.reason)}</div>}

      <h3 class="backup-list-title">{t('On the server')}</h3>
      <div class="backup-folder" data-testid="backup-folder">
        <span class="muted">{t('Folder')}</span>
        <code>{folder ?? '…'}</code>
        {!b.managed_by && !editing && (
          <button type="button" class="btn btn-small" onClick={openFolder} disabled={inProgress || !b.can_backup} data-testid="backup-folder-change">{t('Change')}</button>
        )}
      </div>
      {editing && (
        <div class="setting-edit backup-folder-edit" data-testid="backup-folder-edit">
          <label class="field">
            <span>{t('Folder on the server')}</span>
            <input type="text" value={dir} onInput={(e) => setDir((e.target as HTMLInputElement).value)} placeholder="/mnt/usb/solis" spellcheck={false} data-testid="backup-folder-input" />
          </label>
          <label class="field field-short">
            <span>{t('Keep the newest')}</span>
            <input type="number" min={b.folder.keep_min} max={b.folder.keep_max} value={keep} onInput={(e) => setKeep((e.target as HTMLInputElement).value)} data-testid="backup-keep-input" />
          </label>
          <div class="setting-hint">
            {t('An existing, writable folder under /mnt, /media, /srv, /home, /opt, /var/backups, /backup, /backups or /data, such as a mounted USB drive or network share. The dashboard can download only from its own folder; files elsewhere are listed, and you copy them from there.')}
          </div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-primary" onClick={saveFolder} disabled={busy} data-testid="backup-folder-save">{busy ? t('Asking the server…') : t('Save')}</button>
            <button type="button" class="btn" onClick={() => setEditing(false)} disabled={busy}>{t('Cancel')}</button>
          </div>
          {formError && <div class="field-error" role="alert" data-testid="backup-folder-error">{formError}</div>}
        </div>
      )}
      {!b.listed ? (
        <div class="setting-hint">{t('The server does not show its backups folder to the dashboard. On the server run: solis-monitor update')}</div>
      ) : b.backups.length === 0 ? (
        <div class="setting-hint" data-testid="backup-empty">{t('No backups yet.')}</div>
      ) : (
        <ul class="backup-list" data-testid="backup-list">
          {b.backups.map((x) => (
            <li key={x.name} class={removing === x.name ? 'removing' : ''}>
              <span class="backup-when">{when(x.created)}</span>
              <span class="muted">{formatSize(x.size)}</span>
              {removing === x.name ? (
                <span class="backup-confirm" role="alertdialog" data-testid="backup-remove-confirm">
                  <span>{t('Remove this backup? It cannot be undone.')}</span>
                  <button type="button" class="btn btn-small btn-danger" onClick={() => remove(x.name)} disabled={busy} data-testid="backup-remove-yes">{busy ? t('Asking the server…') : t('Remove')}</button>
                  <button type="button" class="btn btn-small" onClick={() => setRemoving(null)} disabled={busy}>{t('Cancel')}</button>
                </span>
              ) : (
                <>
                  {x.downloadable
                    ? <a class="btn btn-small" href={downloadUrl(x.name)} download={x.name} data-testid="backup-download">{t('Download')}</a>
                    : <span class="muted backup-elsewhere" data-testid="backup-elsewhere">{t('on the server')}</span>}
                  {!b.managed_by && (
                    <button type="button" class="btn btn-small btn-quiet" onClick={() => setRemoving(x.name)} disabled={inProgress || !b.can_backup}
                            aria-label={t('Remove')} title={t('Remove')} data-testid="backup-remove">✕</button>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {elsewhere && <div class="setting-hint">{t('Files in {dir} can be copied from the server; the dashboard can download only from its own folder.', { dir: folder ?? '' })}</div>}
      {b.listed && <div class="setting-hint">{t('The server keeps the newest {n} made from here; older ones are removed. Backups made on the server by hand (solis-monitor backup) stay where you put them.', { n: b.folder.keep })}</div>}
      <div class="setting-hint">{t('To put a backup back, on the server run: solis-monitor restore FILE')}</div>
    </Card>
  );
}
