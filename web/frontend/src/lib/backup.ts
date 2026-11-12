import { t, tx } from './i18n';
import type { Tone } from './update';

/** Shape of /api/settings/backups (web/backend/app/routes/backups.py). */
export interface BackupStatus {
  state: 'running' | 'ok' | 'failed';
  message: string | null;
  file: string | null;
  started: string | null;
  finished: string | null;
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
    case 'ok': return { text: s.file ? t('Backup made: {file}', { file: s.file }) : s.message ? t(s.message) : t('Backup made.'), tone: 'ok' };
    default: return { text: t('Backup failed: {message}', { message: s.message ? t(s.message) : t('see the server log') }), tone: 'bad' };
  }
}

/** The download link of one archive (the name is what the server listed, nothing else is accepted there). */
export const downloadUrl = (name: string): string => `/api/settings/backups/${encodeURIComponent(name)}`;
