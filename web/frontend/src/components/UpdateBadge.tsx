import { goTo } from '../lib/nav';
import { usePoll } from '../lib/poll';
import { t } from '../lib/i18n';
import { updateBadge, type VersionPayload } from '../lib/update';

const RECHECK_MS = 10 * 60_000;     // only reads the server's own answer; the server asks GitHub every few hours

/** "Update available" next to the Online pill on every page. It opens Settings -> System -> Software with the update ready to confirm. */
export function UpdateBadge() {
  const badge = updateBadge(usePoll<VersionPayload>('/api/version', RECHECK_MS).data);
  if (!badge) return null;
  return (
    <button type="button" class="pill pill-update" title={badge.title} data-testid="update-badge"
            onClick={() => goTo('settings', { tab: 'system', update: '1' })}>
      {t(badge.label)}
    </button>
  );
}
