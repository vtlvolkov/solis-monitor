import { useState } from 'preact/hooks';
import { ApiError, postJson } from '../lib/api';
import { asText, FUNCTIONS, MAX_COUNT, parseRead, rows, type DebugReply, type FunctionCode } from '../lib/debug';
import { t } from '../lib/i18n';
import { Card } from './common';

const KEEP = 10;                                  // exchanges kept on screen; they are not stored anywhere

type Exchange = DebugReply & { at: string };

/** Copies through a temporary text box and the browser's own copy command; false when that is refused too. */
function copyBySelection(text: string): boolean {
  const box = document.createElement('textarea');
  box.value = text;
  box.setAttribute('readonly', '');
  box.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
  document.body.appendChild(box);
  box.select();
  try { return document.execCommand('copy'); } catch { return false; } finally { box.remove(); }
}

/**
 * Reads registers by hand and shows the request and the answer, for finding out what the inverter reports.
 * Read-only: there is no way to write from here. The poller owns the one Modbus connection, so a read waits
 * its turn between the regular readings.
 */
export function DebugCard() {
  const [fn, setFn] = useState<FunctionCode>(4);
  const [address, setAddress] = useState('33049');
  const [count, setCount] = useState('1');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<Exchange[]>([]);
  const [shown, setShown] = useState(0);              // which exchange of the history is on screen
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState<string | null>(null);       // the text to copy by hand when the browser refuses

  const read = async (fnCode: number, addr: string, cnt: string) => {
    const r = parseRead(fnCode, addr, cnt);
    if (!r.ok) return setError(r.error);
    setBusy(true); setError(null); setCopied(false);
    try {
      const reply = await postJson<DebugReply>('/api/settings/debug/read', r.value);
      setHistory((h) => [{ ...reply, at: new Date().toLocaleTimeString() }, ...h].slice(0, KEEP));
      setShown(0);
    } catch (e) {
      setError(t(e instanceof ApiError ? e.message : 'Request failed'));
    } finally {
      setBusy(false);
    }
  };

  const onSubmit = (e: Event) => { e.preventDefault(); read(fn, address, count); };
  const current = history[shown];

  // navigator.clipboard only exists on https:// and localhost, and the dashboard is usually plain http:// on a home
  // network, so fall back to the old selection copy, and finally to a box the text can be copied from by hand.
  const copy = async () => {
    if (!current) return;
    const text = asText(current);
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); setCopied(true); setManual(null); return; }
    } catch { /* try the next way */ }
    if (copyBySelection(text)) { setCopied(true); setManual(null); } else { setManual(text); }
  };

  const edit = (set: (v: string) => void) => (e: Event) => { set((e.target as HTMLInputElement).value); setError(null); };

  return (
    <Card title="Read registers" class="debug-card">
      <div class="setting-hint">
        {t('Ask the inverter for registers and see exactly what it answers. This only reads: nothing here can change a setting. The poller keeps the only connection to the inverter, so a read waits for its turn between the regular readings.')}
      </div>

      <form class="dbg-form" onSubmit={onSubmit} data-testid="debug-form">
        <label class="dbg-fn"><span>{t('Function')}</span>
          <select value={fn} disabled={busy} data-testid="debug-function" onChange={(e) => setFn(Number((e.target as HTMLSelectElement).value) as FunctionCode)}>
            {FUNCTIONS.map((f) => <option key={f.code} value={f.code}>{t(f.label)}</option>)}
          </select></label>
        <label><span>{t('Address')}</span>
          <input type="text" value={address} disabled={busy} placeholder="33049 / 0x8119" autocomplete="off" spellcheck={false} data-testid="debug-address"
                 onInput={edit(setAddress)} /></label>
        <label><span>{t('Count')} <span class="muted">1–{MAX_COUNT}</span></span>
          <input type="text" inputMode="numeric" value={count} disabled={busy} data-testid="debug-count" onInput={edit(setCount)} /></label>
        <div class="update-actions dbg-actions">
          <button type="submit" class="btn btn-primary" disabled={busy} data-testid="debug-read">{busy ? t('Reading…') : t('Read')}</button>
        </div>
      </form>
      {error && <div class="field-error" role="alert" data-testid="debug-error">{error}</div>}

      {current && (
        <div class="dbg-result" data-testid="debug-result">
          <div class="dbg-summary">
            <strong>FC{String(current.function).padStart(2, '0')}</strong>
            <span>{t('unit {n}', { n: current.unit })}</span>
            <span>{current.address}–{current.address + current.count - 1}</span>
            <span class="muted">{current.ms} ms</span>
            <button type="button" class="btn btn-small" onClick={copy} data-testid="debug-copy">{copied ? t('Copied') : t('Copy as text')}</button>
          </div>
          <div class="dbg-frames">
            <div><span class="muted">{t('Request')}</span><code data-testid="debug-request">{current.request}</code></div>
            <div><span class="muted">{t('Answer')}</span><code data-testid="debug-response">{current.response}</code></div>
          </div>
          {manual !== null && (
            <label class="dbg-manual"><span>{t('Your browser does not allow copying here. Select the text below and copy it yourself.')}</span>
              <textarea readOnly rows={8} value={manual} data-testid="debug-manual"
                        onFocus={(e) => (e.target as HTMLTextAreaElement).select()} /></label>
          )}
          {!current.ok && (
            <div class="update-result tone-bad" role="alert" data-testid="debug-exception">
              {t('The inverter refused: {name} (exception {code}).', { name: t(current.exception ?? ''), code: current.exception_code ?? 0 })}
            </div>
          )}
          {current.ok && current.registers && (
            <div class="dbg-table-wrap">
              <table class="dbg-table" data-testid="debug-table">
                <thead><tr><th>{t('Register')}</th><th>{t('Hex')}</th><th>u16</th><th>s16</th><th>{t('Text')}</th><th title={t('With the next register, high word first')}>u32</th><th>s32</th></tr></thead>
                <tbody>
                  {rows(current.address, current.registers).map((r) => (
                    <tr key={r.address}>
                      <td>{r.address}</td><td>{r.hex}</td><td>{r.u16}</td><td>{r.s16}</td><td>{r.text}</td><td>{r.u32 ?? ''}</td><td>{r.s32 ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {history.length > 1 && (
        <>
          <h3 class="tou-h">{t('This session')}</h3>
          <ul class="dbg-history" data-testid="debug-history">
            {history.map((h, i) => (
              <li key={`${h.at}-${i}`} class={i === shown ? 'on' : ''}>
                <button type="button" class="dbg-pick" onClick={() => setShown(i)}>
                  <span class="muted">{h.at}</span> FC{String(h.function).padStart(2, '0')} {h.address}–{h.address + h.count - 1}
                  {!h.ok && <span class="dbg-bad"> · {t('refused')}</span>}
                </button>
                <button type="button" class="btn btn-small" disabled={busy} onClick={() => {
                  setFn(h.function as FunctionCode); setAddress(String(h.address)); setCount(String(h.count));
                  read(h.function, String(h.address), String(h.count));
                }}>{t('Read again')}</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}
