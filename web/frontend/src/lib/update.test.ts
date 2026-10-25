import { describe, expect, it } from 'vitest';
import { describeStatus, shouldReload, type UpdatePayload, type UpdateStatus } from './update';

const st = (patch: Partial<UpdateStatus>): UpdateStatus =>
  ({ state: 'ok', from: 'v0.1.3', to: 'v0.1.4', message: null, started: null, finished: null, ...patch });
const payload = (patch: Partial<UpdatePayload>): UpdatePayload =>
  ({ version: 'v0.1.4', can_update: true, reason: null, busy: false, latest: null, status: st({}), ...patch });

describe('update status text', () => {
  it('says what is happening or what happened', () => {
    expect(describeStatus(st({ state: 'running' }))).toEqual({ text: 'Updating to v0.1.4…', tone: 'info' });
    expect(describeStatus(st({}))).toEqual({ text: 'Updated to v0.1.4.', tone: 'ok' });
    expect(describeStatus(st({ state: 'rolled_back' })).tone).toBe('warn');
    expect(describeStatus(st({ state: 'rolled_back' })).text).toBe('The update to v0.1.4 did not start properly, so v0.1.3 was restored.');
    expect(describeStatus(st({ state: 'failed', message: 'Boom' }))).toEqual({ text: 'Update failed: Boom', tone: 'bad' });
    expect(describeStatus(st({ state: 'failed' })).text).toContain('see the server log');
  });

  it('does not claim an update when the updater found nothing newer', () => {
    expect(describeStatus(st({ from: 'v0.1.4', to: 'v0.1.4' })).text).toBe('You have the newest version.');
  });
});

describe('reload after an update', () => {
  it('reloads once the update is done and the version changed', () => {
    expect(shouldReload('v0.1.3', payload({}))).toBe(true);
  });
  it('does not reload while busy, after a rollback, or when nothing changed', () => {
    expect(shouldReload('v0.1.3', payload({ busy: true }))).toBe(false);
    expect(shouldReload('v0.1.3', payload({ version: 'v0.1.3', status: st({ state: 'rolled_back' }) }))).toBe(false);
    expect(shouldReload('v0.1.4', payload({}))).toBe(false);
    expect(shouldReload('v0.1.3', payload({ status: null }))).toBe(false);
  });
});
