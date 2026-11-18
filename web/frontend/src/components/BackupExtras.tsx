import { useEffect, useState } from 'preact/hooks';
import {
  describeCopy, isTime, remoteBody, remoteError, remoteWhere,
  type BackupPayload, type RemoteForm, type RemoteKind,
} from '../lib/backup';
import { t } from '../lib/i18n';

/** What the card passes down: send one request to the host (true = accepted), and how the last one of ours went. */
export interface AskProps {
  b: BackupPayload;
  ask: (path: string, body: object, onError: (message: string) => void) => Promise<boolean>;
  disabled: boolean;                                   // another request is running, or backing up is not set up
  pending: boolean;                                    // our request is with the host
  result: { state: 'ok' | 'failed'; message: string | null } | null;   // its answer, once it finished
}

/** Settings → Backup → Daily backup: one backup a day at a time of the server's clock. */
export function ScheduleSection({ b, ask, disabled, pending, result }: AskProps) {
  const saved = b.schedule?.time ?? null;
  const available = b.schedule?.available ?? false;
  const [on, setOn] = useState(saved !== null);
  const [time, setTime] = useState(saved ?? '03:30');
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { setOn(saved !== null); if (saved) setTime(saved); }, [saved]);

  const changed = on !== (saved !== null) || (on && time !== saved);
  const save = async () => {
    if (on && !isTime(time)) { setError(t('That is not a time of day.')); return; }
    setError(null);
    await ask('/api/settings/backups/schedule', { time: on ? time : null }, setError);
  };

  return (
    <div class="backup-section" data-testid="backup-schedule">
      <h3 class="backup-list-title">{t('Daily backup')}</h3>
      <div class="setting-edit backup-schedule">
        <label class="check">
          <input type="checkbox" checked={on} disabled={disabled || (!available && saved === null)}
                 onChange={(e) => setOn((e.target as HTMLInputElement).checked)} data-testid="backup-schedule-on" />
          <span>{t('Make a backup every day at')}</span>
        </label>
        <input type="time" class="backup-time" value={time} disabled={disabled || !on}
               onInput={(e) => setTime((e.target as HTMLInputElement).value)} aria-label={t('Time')} data-testid="backup-schedule-time" />
        <button type="button" class="btn btn-small btn-primary" onClick={save} disabled={disabled || !changed} data-testid="backup-schedule-save">
          {pending ? t('Asking the server…') : t('Save')}
        </button>
      </div>
      {!available && <div class="setting-hint">{t('Daily backups need the timer on the server. On the server run: solis-monitor dashboard-updates on')}</div>}
      <div class="setting-hint">
        {t("The server's own clock is used. A server that is off at that time makes the backup when it starts. It is kept with the others in the folder above (the newest {n}), and copied to the server below if one is set.", { n: b.folder.keep })}
      </div>
      {result?.state === 'failed' && <div class="field-error" role="alert">{result.message ? t(result.message) : t('Request failed')}</div>}
      {error && <div class="field-error" role="alert" data-testid="backup-schedule-error">{error}</div>}
    </div>
  );
}

const EMPTY: RemoteForm = { kind: 'nextcloud', url: '', user: '', password: '', folder: 'Solis Monitor', keep: '14' };

/** Settings → Backup → Copy to Nextcloud or WebDAV: every backup in the folder above is also uploaded there. */
export function RemoteSection({ b, ask, disabled, pending, result }: AskProps) {
  const r = b.remote ?? null;
  const limits = b.remote_keep ?? { min: 1, max: 100, default: 14 };
  const [editing, setEditing] = useState(false);
  const [f, setF] = useState<RemoteForm>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [sentStop, setSentStop] = useState(false);
  const [waiting, setWaiting] = useState(false);       // our request is with the host: its answer decides about the form
  const [fresh, setFresh] = useState(false);           // the form was just opened: an older failure is not shown

  useEffect(() => {                                    // the host answered: close the form when the connection worked
    if (!waiting || !result) return;
    setWaiting(false);
    if (result.state === 'ok') { setEditing(false); setConfirmStop(false); setSentStop(false); setF((x) => ({ ...x, password: '' })); }
  }, [result, waiting]);

  const open = () => {
    setF(r ? { kind: r.kind, url: r.url, user: r.user, password: '', folder: r.folder, keep: String(r.keep) } : { ...EMPTY, keep: String(limits.default) });
    setError(null); setEditing(true); setSentStop(false); setFresh(true);
  };
  const set = (k: keyof RemoteForm) => (e: Event) => setF({ ...f, [k]: (e.target as HTMLInputElement).value });
  const save = async () => {
    const bad = remoteError(f, r !== null, limits);
    if (bad) { setError(bad); return; }
    setError(null); setSentStop(false); setFresh(false);
    if (await ask('/api/settings/backups/remote', remoteBody(f), setError)) setWaiting(true);
  };
  const stop = async () => {
    setSentStop(true); setFresh(false);
    if (await ask('/api/settings/backups/remote/remove', {}, setError)) setWaiting(true);
  };
  const copy = r ? describeCopy(r) : null;
  const failed = !waiting && !fresh && result?.state === 'failed' ? (result.message ? t(result.message) : t('Request failed')) : null;

  return (
    <div class="backup-section" data-testid="backup-remote">
      <h3 class="backup-list-title">{t('Copy to Nextcloud or WebDAV')}</h3>
      {!editing && (r ? (
        <>
          <div class="backup-folder" data-testid="backup-remote-where">
            <code>{remoteWhere(r)}</code>
            <span class="muted">{t('as {user}', { user: r.user })}</span>
          </div>
          {copy && <div class={`update-result tone-${copy.tone}`} role="status" data-testid="backup-remote-last">{copy.text}</div>}
          <div class="setting-hint">{t('Each backup is uploaded there too; the newest {n} of this site are kept there, older ones are removed.', { n: r.keep })}</div>
          {confirmStop ? (
            <div class="backup-confirm" role="alertdialog" data-testid="backup-remote-stop-confirm">
              <span>{t('Stop copying the backups there? The copies already there stay.')}</span>
              <button type="button" class="btn btn-small btn-danger" onClick={stop} disabled={disabled} data-testid="backup-remote-stop-yes">{pending ? t('Asking the server…') : t('Stop copying')}</button>
              <button type="button" class="btn btn-small" onClick={() => setConfirmStop(false)} disabled={pending}>{t('Cancel')}</button>
            </div>
          ) : (
            <div class="confirm-actions">
              <button type="button" class="btn btn-small" onClick={open} disabled={disabled} data-testid="backup-remote-change">{t('Change')}</button>
              <button type="button" class="btn btn-small btn-quiet" onClick={() => setConfirmStop(true)} disabled={disabled} data-testid="backup-remote-stop">{t('Stop copying')}</button>
            </div>
          )}
          {sentStop && failed && <div class="field-error" role="alert">{failed}</div>}
        </>
      ) : (
        <>
          <div class="setting-hint">{t("Each backup can also be uploaded to Nextcloud or another WebDAV server, so it survives when this machine's disk does not.")}</div>
          <div class="confirm-actions">
            <button type="button" class="btn btn-small" onClick={open} disabled={disabled} data-testid="backup-remote-setup">{t('Set up…')}</button>
          </div>
        </>
      ))}
      {editing && (
        <form class="inv-form backup-remote-form" onSubmit={(e) => { e.preventDefault(); save(); }} data-testid="backup-remote-edit">
          <div class="backup-kinds" role="radiogroup" aria-label={t('Server')}>
            {(['nextcloud', 'webdav'] as RemoteKind[]).map((k) => (
              <label class="radio" key={k}>
                <input type="radio" name="backup-remote-kind" checked={f.kind === k} onChange={() => setF({ ...f, kind: k })} data-testid={`backup-remote-kind-${k}`} />
                <span>{k === 'nextcloud' ? 'Nextcloud' : t('Another WebDAV server')}</span>
              </label>
            ))}
          </div>
          <label class="backup-remote-url">
            <span>{f.kind === 'nextcloud' ? t('Nextcloud address') : t('WebDAV folder address')}</span>
            <input type="url" value={f.url} onInput={set('url')} spellcheck={false} autocomplete="off" data-testid="backup-remote-url"
                   placeholder={f.kind === 'nextcloud' ? 'http://umbrel.local:8081' : 'https://dav.example.com/backups'} />
          </label>
          <label class="backup-remote-user">
            <span>{t('User name')}</span>
            <input type="text" value={f.user} onInput={set('user')} spellcheck={false} autocomplete="off" data-testid="backup-remote-user" />
          </label>
          <label class="backup-remote-pass">
            <span>{f.kind === 'nextcloud' ? t('App password') : t('Password')}</span>
            <input type="password" value={f.password} onInput={set('password')} autocomplete="new-password" data-testid="backup-remote-password"
                   placeholder={r ? t('Saved; leave empty to keep it') : ''} />
          </label>
          <label class="backup-remote-folder">
            <span>{t('Folder there')}</span>
            <input type="text" value={f.folder} onInput={set('folder')} spellcheck={false} data-testid="backup-remote-folder" />
          </label>
          <label class="backup-remote-keep">
            <span>{t('Keep the newest')}</span>
            <input type="number" min={limits.min} max={limits.max} value={f.keep} onInput={set('keep')} data-testid="backup-remote-keep" />
          </label>
          <div class="setting-hint">
            {f.kind === 'nextcloud'
              ? t('Use your Nextcloud login name and an app password (in Nextcloud: Settings → Security → Create new app password). The folder is made inside your files.')
              : t('The address of a folder on the WebDAV server; the folder named here is made inside it.')}
          </div>
          <div class="setting-hint">
            {t('Saving tries the connection first: it makes the folder, writes a small test file and removes it. The password is kept on the server only; this page never shows it again.')}
          </div>
          <div class="setting-hint"><strong>{t('The copies contain your passwords and tokens.')}</strong> {t('Do not share that folder.')}</div>
          <div class="confirm-actions">
            <button type="submit" class="btn btn-primary" disabled={disabled} data-testid="backup-remote-save">
              {pending ? t('Trying the connection…') : t('Save')}
            </button>
            <button type="button" class="btn" onClick={() => setEditing(false)} disabled={pending}>{t('Cancel')}</button>
          </div>
          {(error || (!sentStop && failed)) && <div class="field-error" role="alert" data-testid="backup-remote-error">{error ?? failed}</div>}
        </form>
      )}
    </div>
  );
}
