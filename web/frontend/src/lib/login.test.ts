import { describe, expect, it } from 'vitest';
import { describeLogin, parsePasswords } from './login';

const bad = (c: string, n: string, a: string) => { const r = parsePasswords(c, n, a); return r.ok ? null : r.error; };

describe('password form', () => {
  it('accepts a good change', () => {
    expect(parsePasswords('old-password', 'A-new-password-7', 'A-new-password-7')).toEqual({ ok: true, value: { current: 'old-password', next: 'A-new-password-7' } });
    expect(parsePasswords('old', 'пароль-з-пробілом й', 'пароль-з-пробілом й').ok).toBe(true);        // any characters, also non-Latin ones
  });
  it('asks for the current password first', () => {
    expect(bad('', 'A-new-password-7', 'A-new-password-7')).toBe('Enter your current password.');
  });
  it('checks length, control characters, edge spaces and sameness like the server', () => {
    expect(bad('old', 'short', 'short')).toBe('The new password needs 8 to 128 characters.');
    expect(bad('old', 'x'.repeat(129), 'x'.repeat(129))).toBe('The new password needs 8 to 128 characters.');
    expect(bad('old', 'tab\there-1', 'tab\there-1')).toBe('The new password cannot contain control characters.');
    expect(bad('old', ' padded-pass1', ' padded-pass1')).toBe('The new password cannot start or end with a space.');
    expect(bad('A-new-password-7', 'A-new-password-7', 'A-new-password-7')).toBe('The new password is the same as the current one.');
  });
  it('makes you type it twice', () => {
    expect(bad('old', 'A-new-password-7', 'A-new-password-8')).toBe('The two new passwords are different.');
  });
});

describe('where the password comes from', () => {
  const when = (iso: string) => iso.slice(0, 10);
  it('says so', () => {
    expect(describeLogin({ source: 'env', changed_at: null, user: 'admin' }, when)).toBe('User admin. The password is the original one from the setup.');
    expect(describeLogin({ source: 'saved', changed_at: '2026-10-28T11:00:00Z', user: 'admin' }, when)).toBe('User admin. Password changed on this page, 2026-10-28.');
  });
});
