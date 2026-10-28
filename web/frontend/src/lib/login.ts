// The dashboard password: types and pure helpers for the Settings card (unit-tested).
import { t, tx } from './i18n';
import type { Parsed } from './tariff';

export interface LoginPayload {
  user: string;
  source: 'saved' | 'env';
  changed_at: string | null;
  min_length: number; max_length: number;
  platform: string | null;                      // a platform (umbrelOS) that shows its own copy of the original password
  web: { can_change: boolean; reason: string | null };
}

/** Messages the server sends in English; listed so each has a Ukrainian text. */
export const SERVER_TEXTS = [
  tx('The current password is not right.'),
  tx('The new password must be text.'),
  tx('The new password cannot contain control characters.'),
  tx('The new password cannot start or end with a space.'),
  tx('The new password is the same as the current one.'),
  tx('There is no login to change.'),
  tx('This dashboard cannot save a new password.'),
  tx('Could not save the new password on the server.'),
];

export interface NewLogin { current: string; next: string }

/** Validates the form like the server does; the server stays the judge. */
export function parsePasswords(current: string, next: string, again: string, min = 8, max = 128): Parsed<NewLogin> {
  if (!current) return { ok: false, error: t('Enter your current password.') };
  if (next.length < min || next.length > max) return { ok: false, error: t('The new password needs {min} to {max} characters.', { min, max }) };
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(next)) return { ok: false, error: t('The new password cannot contain control characters.') };
  if (next !== next.trim()) return { ok: false, error: t('The new password cannot start or end with a space.') };
  if (next === current) return { ok: false, error: t('The new password is the same as the current one.') };
  if (next !== again) return { ok: false, error: t('The two new passwords are different.') };
  return { ok: true, value: { current, next } };
}

/** One line about where the password in use comes from. */
export function describeLogin(p: Pick<LoginPayload, 'source' | 'changed_at' | 'user'>, when: (iso: string) => string): string {
  return p.source === 'saved' && p.changed_at
    ? t('User {user}. Password changed on this page, {when}.', { user: p.user, when: when(p.changed_at) })
    : t('User {user}. The password is the original one from the setup.', { user: p.user });
}
