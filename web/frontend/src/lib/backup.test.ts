import { describe, expect, it } from 'vitest';
import { describeBackup, downloadUrl, folderError, formatSize } from './backup';

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
});
