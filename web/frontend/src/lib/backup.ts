import { locale, t, tx } from './i18n';
import type { Tone } from './update';

/** Shape of /api/settings/backups (web/backend/app/routes/backups.py). */
export interface BackupStatus {
  state: 'running' | 'ok' | 'failed';
  message: string | null;
  file: string | null;
  started: string | null;
  finished: string | null;
  scheduled?: boolean;            // made by the daily timer, not asked for on this page
  copy?: 'ok' | 'failed' | null;  // how the copy to Nextcloud/WebDAV went (null: none was made)
  copy_message?: string | null;
}
export type RemoteKind = 'nextcloud' | 'webdav';
export interface BackupRemote {
  kind: RemoteKind;
  url: string;                    // Nextcloud: the server's address; WebDAV: the address of a folder there
  user: string;
  folder: string;                 // inside the user's files (Nextcloud) or inside that folder (WebDAV)
  keep: number;
  last: { state: 'ok' | 'failed'; message: string | null; file: string | null; at: string | null } | null;
}
export interface BackupEntry { name: string; size: number; created: string; downloadable: boolean }
export interface BackupFolder {
  dir: string | null;             // where the archives go (null until the server has said)
  default_dir: string | null;     // the server's own backups/ folder, the one the dashboard can download from
  keep: number;                   // how many dashboard-made archives the server keeps there
  keep_min: number;
  keep_max: number;
}
export interface BackupPayload {
  can_backup: boolean;
  reason: string | null;
  managed_by: string | null;      // a platform (umbrelOS) that backs the app up itself: no button then
  busy: boolean;
  status: BackupStatus | null;
  backups: BackupEntry[];
  listed: boolean;                // false when the server's backups folder is not mounted (an older compose file)
  folder: BackupFolder;
  schedule?: { time: string | null; available: boolean };   // daily backup at HH:MM; available = the server's timer is there
  remote?: BackupRemote | null;                              // where each backup is copied (never the password)
  remote_keep?: { min: number; max: number; default: number };
}

/** "HH:MM", 00:00 to 23:59, as the server accepts it. */
export const isTime = (v: string): boolean => /^([01]\d|2[0-3]):[0-5]\d$/.test(v);

/** The address of any Nextcloud page cut to the server's own (a subfolder install keeps its subfolder), as the server does. */
export function nextcloudBase(url: string): string {
  const base = url.trim().split('?')[0].split('#')[0].replace(/\/+$/, '');
  return base.replace(/\/(index\.php|apps|remote\.php|login|s|f|settings)(\/.*)?$/, '').replace(/\/+$/, '');
}

export interface RemoteForm { kind: RemoteKind; url: string; user: string; password: string; folder: string; keep: string }

/** The server's rules (routes/backups.py remote_error), checked before asking it. `saved`: a password is kept there. */
export function remoteError(f: RemoteForm, saved: boolean, keep: { min: number; max: number }): string | null {
  const url = (f.kind === 'nextcloud' ? nextcloudBase(f.url) : f.url.trim()).replace(/\/+$/, '');
  if (!url) return t('Enter the address of the server.');
  if (url.length > 300 || !/^https?:\/\/[A-Za-z0-9.-]+(:\d{1,5})?(\/[A-Za-z0-9._~%/+-]*)?$/.test(url)) return t('The address must start with http:// or https://.');
  const user = f.user.trim();
  if (!user || user.length > 200 || user.includes(':') || /[\x00-\x1f\x7f]/.test(user)) return t('Enter the user name.');
  if ((!f.password && !saved) || f.password.length > 500 || /[\x00-\x1f\x7f]/.test(f.password)) return t('Enter the password.');
  const folder = f.folder.trim().replace(/^\/+|\/+$/g, '');
  if (folder.length > 200 || /[\x00-\x1f\x7f"\\]/.test(folder) || folder.split('/').some((s) => s === '.' || s === '..')) return t('That folder name is not allowed.');
  const n = Number(f.keep);
  if (!Number.isInteger(n) || n < keep.min || n > keep.max) return t('Keep must be a whole number from {min} to {max}.', { min: keep.min, max: keep.max });
  return null;
}

/** What is sent to POST /api/settings/backups/remote: trimmed, the Nextcloud address cut; "" keeps the saved password. */
export function remoteBody(f: RemoteForm) {
  return {
    kind: f.kind,
    url: (f.kind === 'nextcloud' ? nextcloudBase(f.url) : f.url.trim()).replace(/\/+$/, ''),
    user: f.user.trim(),
    password: f.password,
    folder: f.folder.trim().replace(/^\/+|\/+$/g, ''),
    keep: Number(f.keep),
  };
}

/** Where the copies go, in one line: "Nextcloud · http://… · Backups/solis". */
export function remoteWhere(r: BackupRemote): string {
  return [r.kind === 'nextcloud' ? 'Nextcloud' : t('WebDAV'), r.url, r.folder || '/'].join(' · ');
}

/** How the last copy went, for the line under the server. */
export function describeCopy(r: BackupRemote): { text: string; tone: Tone } | null {
  if (!r.last) return null;
  const at = r.last.at ? new Date(r.last.at).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' }) : '';
  return r.last.state === 'ok'
    ? { text: t('Last copy: {when}', { when: at }), tone: 'ok' }
    : { text: t('The last copy failed ({when}): {message}', { when: at, message: r.last.message ? t(r.last.message) : t('see the server log') }), tone: 'bad' };
}

/** The same shape check as the server's: an absolute path without spaces or "..". The server then checks the
 *  folder itself (it must exist, be writable and sit under a mount-point-like root). */
export function folderError(dir: string): string | null {
  const d = dir.trim();
  if (!d) return t('Enter a folder on the server, like /mnt/usb/solis.');
  if (d.length > 200 || !/^\/[A-Za-z0-9._@+-]+(\/[A-Za-z0-9._@+-]+)*\/?$/.test(d) || /(^|\/)\.\.(\/|$)/.test(d)) {
    return t('The folder must be an absolute path on the server, like /mnt/usb/solis.');
  }
  return null;
}

/** Messages the server and the backup helper send in English; listed here so each one has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx('A backup is already running.'),
  tx('Could not reach the backup helper on the server.'),
  tx('Backing up from the dashboard is not set up. On the server run: solis-monitor dashboard-updates on'),
  tx('The backup stopped without reporting. On the server, see: solis-monitor logs'),
  tx('The backup stopped before it finished. See: solis-monitor logs'),
  tx('Backing up the database'),
  tx('Packing the archive'),
  tx('Backup written'),
  tx('No such backup.'),
  tx('That folder name is not allowed.'),
  tx('That folder does not exist on the server. Create it, or mount the drive, first.'),
  tx('That folder is not writable for the backup helper.'),
  tx('The folder must be an absolute path on the server, like /mnt/usb/solis.'),
  tx('That is not the name of a backup.'),
  tx('Backing up the current state first'),
  tx('Stopping the poller and the dashboard'),
  tx('Restoring the database'),
  tx('Starting with the restored settings'),
  tx('The server does not know that request.'),
  tx('That is not a time of day.'),
  tx('Daily backups need the timer on the server. On the server run: solis-monitor dashboard-updates on'),
  tx('Daily backups are on.'),
  tx('Daily backups are off.'),
  tx('Trying the connection'),
  tx('Connected. Backups are copied there from now on.'),
  tx('Backups are no longer copied to a server.'),
  tx('Copying to Nextcloud'),
  tx('Copying to the WebDAV server'),
  tx('Copied to Nextcloud.'),
  tx('Copied to the WebDAV server.'),
  tx('The server could not be reached.'),
  tx('The server broke the connection off. It may limit how much one request can send.'),
  tx('The server did not answer in time.'),
  tx('The server refused the user name or password.'),
  tx('The server does not let this user write there.'),
  tx('That address or folder was not found on the server.'),
  tx('The server refused a file this large.'),
  tx('The server has no room left.'),
  tx('The copy on the server is incomplete.'),
  tx('Choose Nextcloud or another WebDAV server.'),
  tx('The address must start with http:// or https://.'),
  tx('Enter the user name.'),
  tx('Enter the password.'),
];

/** 1234567 -> "1.2 MB"; whole kB below a megabyte. */
export function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  if (bytes < 1024) return t('{n} B', { n: Math.round(bytes) });
  if (bytes < 1024 * 1024) return t('{n} kB', { n: Math.round(bytes / 1024) });
  if (bytes < 1024 * 1024 * 1024) return t('{n} MB', { n: (bytes / 1024 / 1024).toFixed(1) });
  return t('{n} GB', { n: (bytes / 1024 / 1024 / 1024).toFixed(2) });
}

/** One sentence and a tone for a backup's progress or result. */
export function describeBackup(s: BackupStatus): { text: string; tone: Tone } {
  switch (s.state) {
    case 'running': return { text: s.message ? t(s.message) : t('Backing up…'), tone: 'info' };
    case 'ok': {
      const made = s.file ? (s.scheduled ? t('Daily backup made: {file}', { file: s.file }) : t('Backup made: {file}', { file: s.file })) : s.message ? t(s.message) : t('Backup made.');
      if (s.copy === 'failed') {
        return { text: `${made} ${t('It could not be copied to the server: {message}', { message: s.copy_message ? t(s.copy_message) : t('see the server log') })}`, tone: 'warn' };
      }
      return { text: s.copy === 'ok' && s.copy_message ? `${made} ${t(s.copy_message)}` : made, tone: 'ok' };
    }
    default: return { text: t('Backup failed: {message}', { message: s.message ? t(s.message) : t('see the server log') }), tone: 'bad' };
  }
}

/** The download link of one archive (the name is what the server listed, nothing else is accepted there). */
export const downloadUrl = (name: string): string => `/api/settings/backups/${encodeURIComponent(name)}`;
