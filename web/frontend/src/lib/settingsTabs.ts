/** Sections of the Settings page; the chosen one lives in the URL (?view=settings&tab=battery). */
export const SETTINGS_TABS = ['general', 'battery', 'schedule', 'system', 'debug'] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** The tab named in the URL; the header's "Update available" badge (?update=1) opens System; otherwise General. */
export function tabFrom(search: string): SettingsTab {
  const q = new URLSearchParams(search);
  const tab = q.get('tab');
  if ((SETTINGS_TABS as readonly string[]).includes(tab ?? '')) return tab as SettingsTab;
  return q.get('update') ? 'system' : 'general';
}
