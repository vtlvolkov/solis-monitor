import { describe, expect, it } from 'vitest';
import { describeBackup, describeCopy, downloadUrl, folderError, formatSize, isTime, nextcloudBase, remoteBody, remoteError, remoteWhere, type RemoteForm } from './backup';

describe('backup helpers', () => {
  it('formats sizes', () => {
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(20 * 1024)).toBe('20 kB');
    expect(formatSize(1_300_000)).toBe('1.2 MB');
    expect(formatSize(2.5 * 1024 ** 3)).toBe('2.50 GB');
    expect(formatSize(-1)).toBe('');
  });
  it('describes progress and results', () => {
    expect(describeBackup({ state: 'running', message: 'Backing up the database', file: null, started: null, finished: null }))
      .toEqual({ text: 'Backing up the database', tone: 'info' });
    expect(describeBackup({ state: 'ok', message: 'Backup written', file: 'solis-monitor-20261112-180000.tar.gz', started: null, finished: null }).text)
      .toBe('Backup made: solis-monitor-20261112-180000.tar.gz');
    expect(describeBackup({ state: 'failed', message: null, file: null, started: null, finished: null }))
      .toEqual({ text: 'Backup failed: see the server log', tone: 'bad' });
  });
  it('checks the folder the way the server does, before asking it', () => {
    expect(folderError('/mnt/usb/solis')).toBeNull();
    expect(folderError('/mnt/usb/solis/')).toBeNull();
    expect(folderError('  /opt/solis-monitor/backups ')).toBeNull();
    expect(folderError('')).toMatch(/Enter a folder/);
    for (const bad of ['relative/x', '/mnt/../etc', '/mnt/a b', '/mnt/usb/..', 'C:\\backups', '/mnt/' + 'x'.repeat(200)]) {
      expect(folderError(bad), bad).toMatch(/absolute path/);
    }
  });
  it('a folder change or a removal reports through the same status', () => {
    expect(describeBackup({ state: 'ok', message: 'Folder set to /mnt/usb/solis', file: null, started: null, finished: null }).text)
      .toBe('Folder set to /mnt/usb/solis');
    expect(describeBackup({ state: 'ok', message: 'Removed solis-monitor-20261112-180000.tar.gz', file: null, started: null, finished: null }).text)
      .toBe('Removed solis-monitor-20261112-180000.tar.gz');
    expect(describeBackup({ state: 'ok', message: 'Restored from solis-monitor-20261112-180000.tar.gz', file: null, started: null, finished: null }).text)
      .toBe('Restored from solis-monitor-20261112-180000.tar.gz');
  });
  it('builds the download link from the listed name only', () => {
    expect(downloadUrl('solis-monitor-Home-20261112-180000.tar.gz')).toBe('/api/settings/backups/solis-monitor-Home-20261112-180000.tar.gz');
    expect(downloadUrl('a/b')).toBe('/api/settings/backups/a%2Fb');
  });
  it('checks a daily time as the server does', () => {
    for (const ok of ['00:00', '03:30', '23:59']) expect(isTime(ok), ok).toBe(true);
    for (const bad of ['3:30', '24:00', '12:60', '03:30 ', '']) expect(isTime(bad), bad).toBe(false);
  });
  it('a scheduled backup and the copy are described too', () => {
    const base = { state: 'ok' as const, message: 'Backup written', file: 'solis-monitor-20261112-033000.tar.gz', started: null, finished: null };
    expect(describeBackup({ ...base, scheduled: true }).text).toBe('Daily backup made: solis-monitor-20261112-033000.tar.gz');
    expect(describeBackup({ ...base, copy: 'ok', copy_message: 'Copied to Nextcloud.' }))
      .toEqual({ text: 'Backup made: solis-monitor-20261112-033000.tar.gz Copied to Nextcloud.', tone: 'ok' });
    expect(describeBackup({ ...base, copy: 'failed', copy_message: 'The server could not be reached.' }))
      .toEqual({ text: 'Backup made: solis-monitor-20261112-033000.tar.gz It could not be copied to the server: The server could not be reached.', tone: 'warn' });
  });
  it('cuts the address of any Nextcloud page to the server, as the server does', () => {
    expect(nextcloudBase('https://cloud.example.com/index.php/apps/files/?dir=/Backups')).toBe('https://cloud.example.com');
    expect(nextcloudBase('http://100.106.168.116:8081/apps/dashboard/')).toBe('http://100.106.168.116:8081');
    expect(nextcloudBase('https://example.com/nextcloud/remote.php/dav/files/v/')).toBe('https://example.com/nextcloud');
    expect(nextcloudBase(' http://umbrel.local:8081/ ')).toBe('http://umbrel.local:8081');
  });
  it('checks the copy settings before asking the server, and sends them trimmed', () => {
    const keep = { min: 1, max: 100 };
    const f: RemoteForm = { kind: 'nextcloud', url: 'http://umbrel.local:8081/apps/files/', user: ' vitalii ', password: 'abc-def', folder: '/Backups/Сонце/', keep: '14' };
    expect(remoteError(f, false, keep)).toBeNull();
    expect(remoteBody(f)).toEqual({ kind: 'nextcloud', url: 'http://umbrel.local:8081', user: 'vitalii', password: 'abc-def', folder: 'Backups/Сонце', keep: 14 });
    expect(remoteError({ ...f, url: '' }, false, keep)).toMatch(/Enter the address/);
    for (const url of ['ftp://x', 'umbrel.local', 'http://a b', 'http://x/$(id)']) expect(remoteError({ ...f, url }, false, keep), url).toMatch(/http:\/\//);
    for (const user of ['', 'a:b']) expect(remoteError({ ...f, user }, false, keep), user).toMatch(/user name/);
    expect(remoteError({ ...f, password: '' }, false, keep)).toMatch(/password/);
    expect(remoteError({ ...f, password: '' }, true, keep)).toBeNull();          // the saved one is kept
    for (const folder of ['a/../b', 'a"b', 'a\\b']) expect(remoteError({ ...f, folder }, false, keep), folder).toMatch(/folder name/);
    for (const k of ['0', '101', '1.5', '']) expect(remoteError({ ...f, keep: k }, false, keep), k).toMatch(/whole number/);
    expect(remoteBody({ ...f, kind: 'webdav', url: 'https://dav.example.com/remote.php/x/' }).url).toBe('https://dav.example.com/remote.php/x');
  });
  it('says where the copies go and how the last one went', () => {
    const r = { kind: 'nextcloud' as const, url: 'http://nc:8081', user: 'v', folder: 'Backups', keep: 14, last: null };
    expect(remoteWhere(r)).toBe('Nextcloud · http://nc:8081 · Backups');
    expect(remoteWhere({ ...r, kind: 'webdav', folder: '' })).toBe('WebDAV · http://nc:8081 · /');
    expect(describeCopy(r)).toBeNull();
    expect(describeCopy({ ...r, last: { state: 'ok', message: 'Copied to Nextcloud.', file: null, at: '2026-11-12T01:31:00Z' } })?.tone).toBe('ok');
    const bad = describeCopy({ ...r, last: { state: 'failed', message: 'The server could not be reached.', file: null, at: null } });
    expect(bad).toEqual({ text: 'The last copy failed (): The server could not be reached.', tone: 'bad' });
  });
});
