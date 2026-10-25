import { describe, expect, it } from 'vitest';
import { tabFrom } from './settingsTabs';

describe('settings tab from the URL', () => {
  it('opens General by default', () => {
    expect(tabFrom('')).toBe('general');
    expect(tabFrom('?view=settings')).toBe('general');
  });
  it('opens the named tab', () => {
    expect(tabFrom('?view=settings&tab=battery')).toBe('battery');
    expect(tabFrom('?view=settings&tab=schedule')).toBe('schedule');
    expect(tabFrom('?view=settings&tab=system')).toBe('system');
  });
  it('ignores a tab that does not exist', () => {
    expect(tabFrom('?view=settings&tab=nope')).toBe('general');
    expect(tabFrom('?tab=__proto__')).toBe('general');
  });
  it('opens System for the update link (the Software card lives there)', () => {
    expect(tabFrom('?view=settings&update=1')).toBe('system');
    expect(tabFrom('?view=settings&tab=battery&update=1')).toBe('battery');
  });
});
