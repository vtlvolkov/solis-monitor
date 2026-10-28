import { t, tx } from './i18n';

/** Shape of /api/settings/update (see web/backend/app/main.py). */
export interface UpdateStatus {
  state: 'running' | 'ok' | 'rolled_back' | 'failed';
  from: string | null;
  to: string | null;
  message: string | null;
  percent: number | null;
  started: string | null;
  finished: string | null;
}
export interface UpdatePayload {
  version: string | null;
  can_update: boolean;
  reason: string | null;
  managed_by: string | null;      // a platform (umbrelOS) that updates the app itself: no buttons then
  busy: boolean;
  latest: { tag: string; url: string | null; published: string | null; checked_at: string; available: boolean } | null;
  status: UpdateStatus | null;
}

/** Messages the server and the updater send in English; listed here so each one has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx('Could not reach GitHub to look for a new version.'),
  tx('You already have the newest version.'),
  tx('An update is already running.'),
  tx('Could not reach the updater on the server.'),
  tx('This copy was built from source, so it cannot update itself. Update it with git.'),
  tx('This copy was built from source, so it has no releases to compare with.'),
  tx('Updating from the dashboard is not set up. On the server run: solis-monitor dashboard-updates on'),
  tx('The updater stopped without reporting. On the server, see: solis-monitor logs'),
  tx('The update stopped before it finished. See: solis-monitor logs'),
];

export type Tone = 'ok' | 'warn' | 'bad' | 'info';

/** One sentence and a tone for an update's progress or result. */
export function describeStatus(s: UpdateStatus): { text: string; tone: Tone } {
  const to = s.to ?? '?';
  const from = s.from ?? '?';
  switch (s.state) {
    case 'running': return { text: t('Updating to {version}…', { version: to }), tone: 'info' };
    case 'ok': return { text: s.to && s.to === s.from ? t('You have the newest version.') : t('Updated to {version}.', { version: to }), tone: 'ok' };
    case 'rolled_back': return { text: t('The update to {version} did not start properly, so {from} was restored.', { version: to, from }), tone: 'warn' };
    default: return { text: t('Update failed: {message}', { message: s.message ? t(s.message) : t('see the server log') }), tone: 'bad' };
  }
}

/** True when the page should reload to pick up the new dashboard: the update finished and the version moved. */
export const shouldReload = (before: string | null, now: UpdatePayload): boolean =>
  !now.busy && now.status?.state === 'ok' && now.version !== null && now.version !== before;

/** Shape of the public /api/version: the badge only needs this. */
export interface VersionPayload {
  version: string | null;
  installed: boolean;
  update: { available: boolean; tag: string | null };
}

/** The "Update available" badge in the header, or null when there is nothing to offer. */
export function updateBadge(v: VersionPayload | null): { label: string; title: string } | null {
  if (!v?.update?.available || !v.update.tag) return null;
  return { label: t('Update available'), title: t('{version} is available. Click to review and update.', { version: v.update.tag }) };
}

/** The progress bar: a percentage (null = unknown, shown as a moving stripe) and a label for the current step. */
export function progressOf(status: UpdateStatus | null): { percent: number | null; label: string } {
  if (!status || status.state !== 'running') return { percent: null, label: t('Waiting for the server to start the update…') };
  const percent = typeof status.percent === 'number' ? Math.max(0, Math.min(100, Math.round(status.percent))) : null;
  return { percent, label: status.message ? t(status.message) : describeStatus(status).text };
}

/** Steps the updater reports (English, from the server); the ones that name a version are patterns in i18n.uk.ts. */
export const STEP_TEXTS = [
  tx('Looking for the newest release'),
  tx('Downloading the poller image'),
  tx('Downloading the web image'),
  tx('Waiting for the new version to start'),
];
