import { useEffect, useMemo, useState } from 'preact/hooks';
import type { Range } from '../lib/api';
import { t } from '../lib/i18n';
import {
  COLUMN_GROUPS, RESOLUTIONS, allowedResolutions, describePeriod, estimateRows, exportUrl, formatRows, periodSeconds, savedSeparator,
  type ExportRequest, type Resolution, type Separator,
} from '../lib/export';

const SEP_KEY = 'solis-csv-sep';

/**
 * "Export CSV" on the Charts page: the period (the shown range, or a span of days), the resolution, which column
 * groups, and the separator Excel expects. The download itself is a plain link to /api/export.csv, so the browser
 * saves the file and nothing is held in memory here.
 */
export function ExportDialog({ range, day, onClose }: { range: Range; day: string | null; onClose: () => void }) {
  const today = new Date().toISOString().slice(0, 10);
  const [mode, setMode] = useState<'range' | 'days'>(day ? 'days' : 'range');
  const [from, setFrom] = useState(day ?? today);
  const [to, setTo] = useState(day ?? today);
  const [resolution, setResolution] = useState<Resolution>(day ? '1m' : range === '1h' ? '10s' : range === '6h' ? '1m' : range === '24h' ? '15m' : '1h');
  const [groups, setGroups] = useState<string[]>(['power', 'battery', 'grid']);
  const [separator, setSeparator] = useState<Separator>(savedSeparator);

  const period: ExportRequest['period'] = mode === 'range' ? { range } : { from, to };
  const seconds = periodSeconds(period);
  const allowed = useMemo(() => allowedResolutions(seconds), [seconds]);
  useEffect(() => { if (!allowed.includes(resolution)) setResolution(allowed[0] ?? '1h'); }, [allowed, resolution]);
  useEffect(() => { try { localStorage.setItem(SEP_KEY, separator); } catch { /* storage blocked */ } }, [separator]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [onClose]);

  const valid = seconds > 0 && groups.length > 0 && to <= today;
  const url = valid ? exportUrl({ period, resolution, groups, separator }) : '#';
  const toggle = (k: string) => setGroups((g) => (g.includes(k) ? g.filter((x) => x !== k) : [...g, k]));

  return (
    <div class="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-testid="export-dialog">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="export-title">
        <div class="modal-head">
          <h2 id="export-title">{t('Export CSV')}</h2>
          <button type="button" class="btn btn-small btn-quiet" onClick={onClose} aria-label={t('Close')}>✕</button>
        </div>

        <div class="export-section">
          <div class="export-label">{t('Period')}</div>
          <label class="radio"><input type="radio" name="period" checked={mode === 'range'} onChange={() => setMode('range')} />
            <span>{t('The shown range: {range}', { range: t(range) })}</span></label>
          <label class="radio"><input type="radio" name="period" checked={mode === 'days'} onChange={() => setMode('days')} />
            <span>{t('Days')}</span>
            <span class="export-dates">
              <input type="date" value={from} max={today} onInput={(e) => { const v = (e.target as HTMLInputElement).value; setFrom(v); if (v > to) setTo(v); setMode('days'); }} data-testid="export-from" />
              <span class="muted">–</span>
              <input type="date" value={to} min={from} max={today} onInput={(e) => { setTo((e.target as HTMLInputElement).value); setMode('days'); }} data-testid="export-to" />
            </span></label>
        </div>

        <div class="export-section">
          <div class="export-label">{t('Resolution')}</div>
          <div class="seg seg-small" role="group" aria-label={t('Resolution')}>
            {RESOLUTIONS.map((r) => (
              <button type="button" key={r.key} class={r.key === resolution ? 'on' : ''} aria-pressed={r.key === resolution}
                      disabled={!allowed.includes(r.key)} onClick={() => setResolution(r.key)} data-testid={`export-res-${r.key}`}>{t(r.label)}</button>
            ))}
          </div>
          <div class="setting-hint">{resolution === '1d'
            ? t('One row per day with the daily energy totals (kWh); power and voltage columns are the daily averages.')
            : t('Averages over each step. 10 s is the raw reading and is offered up to one day; 1 min up to a week.')}</div>
        </div>

        <div class="export-section">
          <div class="export-label">{t('Columns')}</div>
          <div class="export-groups">
            {COLUMN_GROUPS.map((g) => (
              <label class="check" key={g.key}>
                <input type="checkbox" checked={groups.includes(g.key)} onChange={() => toggle(g.key)} data-testid={`export-group-${g.key}`} />
                <span><strong>{t(g.label)}</strong> <span class="muted">{t(g.hint)}</span></span>
              </label>
            ))}
          </div>
          <div class="setting-hint">{t('Every row starts with the time (ISO 8601, local time zone) and the Unix time in seconds.')}</div>
        </div>

        <div class="export-section">
          <div class="export-label">{t('Format')}</div>
          <label class="radio"><input type="radio" name="sep" checked={separator === 'comma'} onChange={() => setSeparator('comma')} />
            <span>{t('Standard CSV')} <span class="muted">{t('(comma between columns, point as decimal)')}</span></span></label>
          <label class="radio"><input type="radio" name="sep" checked={separator === 'semicolon'} onChange={() => setSeparator('semicolon')} />
            <span>{t('Excel, Ukrainian / European')} <span class="muted">{t('(semicolon between columns, comma as decimal)')}</span></span></label>
        </div>

        <div class="modal-actions">
          <a class={`btn btn-primary ${valid ? '' : 'disabled'}`} href={url} download="" aria-disabled={!valid}
             onClick={(e) => { if (!valid) e.preventDefault(); else setTimeout(onClose, 300); }} data-testid="export-download">
            {t('Download CSV')}
          </a>
          <button type="button" class="btn" onClick={onClose}>{t('Cancel')}</button>
          <span class="muted export-estimate" data-testid="export-estimate">
            {valid ? `${describePeriod(period)} · ${formatRows(estimateRows(seconds, resolution))}` : groups.length === 0 ? t('Choose at least one column group.') : t('Choose a period that is not in the future.')}
          </span>
        </div>
      </div>
    </div>
  );
}
