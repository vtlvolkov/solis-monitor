// Customize on the Overview: the two columns of cards, moved by dragging a card's handle (mouse or touch) or with
// the arrow keys, removed with ×, added from a list. The layout is kept on the server (routes/layout.py).
import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import { ApiError, getJson, postJson } from '../lib/api';
import { t } from '../lib/i18n';
import {
  CARDS, cardInfo, defaultLayout, dropIndex, moveCard, nearestColumn, normalize, onPage, removeCard,
  sameLayout, stepCard, toSave, type Col, type Layout,
} from '../lib/layout';
import { usePoll } from '../lib/poll';

export interface LayoutPayload { layout: Layout | null; web: { can_change: boolean; reason: string | null } }

const HINT = 'solis-overview-layout';     // the last layout seen, so the first paint has the right cards (the server decides)
function readHint(): Layout | null | undefined {
  try { const raw = localStorage.getItem(HINT); return raw == null ? undefined : normalize(JSON.parse(raw)); } catch { return undefined; }
}
function writeHint(l: Layout | null) {
  try { localStorage.setItem(HINT, JSON.stringify(l)); } catch { /* not kept: fine */ }
}

/** The saved layout (null = the default), re-read every 5 minutes so a change made on another screen arrives. */
export function useSavedLayout() {
  const p = usePoll<LayoutPayload>('/api/overview-layout', 300_000);
  const [hint] = useState(readHint);
  useEffect(() => { if (p.data) writeHint(p.data.layout ? normalize(p.data.layout) : null); }, [p.data]);
  const layout = p.data ? (p.data.layout ? normalize(p.data.layout) : null) : hint ?? null;
  return { layout, known: !!p.data || hint !== undefined, reload: p.reload };
}

type Editing = { layout: Layout; before: Layout; web: LayoutPayload['web'] };

/** Edit mode: entering asks for the Settings login (the browser prompts) so it comes before the changes. */
export function useLayoutEditor(current: Layout, outages: boolean, reload: () => Promise<boolean>) {
  const [edit, setEdit] = useState<Editing | null>(null);
  const [state, setState] = useState<{ busy: boolean; error: string | null; signIn: boolean }>({ busy: false, error: null, signIn: false });
  const start = async () => {
    setState({ busy: true, error: null, signIn: false });
    try {
      const p = await getJson<LayoutPayload>('/api/settings/overview-layout');
      setEdit({ layout: current, before: current, web: p.web });
      setState({ busy: false, error: null, signIn: false });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setState({ busy: false, signIn: status === 401, error: status === 401 ? 'Sign in to change the Overview.' : e instanceof Error ? e.message : 'Request failed' });
    }
  };
  const save = async () => {
    if (!edit) return;
    setState({ busy: true, error: null, signIn: false });
    try {
      const p = await postJson<LayoutPayload>('/api/settings/overview-layout', { layout: toSave(edit.layout, outages) });
      writeHint(p.layout ? normalize(p.layout) : null);
      await reload();
      setEdit(null);
      setState({ busy: false, error: null, signIn: false });
    } catch (e) {
      setState({ busy: false, signIn: false, error: e instanceof Error ? e.message : 'Request failed' });
    }
  };
  return {
    edit, state, start, save,
    set: (layout: Layout) => setEdit((e) => (e ? { ...e, layout } : e)),
    cancel: () => { setEdit(null); setState({ busy: false, error: null, signIn: false }); },
    dismiss: () => setState({ busy: false, error: null, signIn: false }),
  };
}

export function EditBar({ edit, state, outages, onAdd, onReset, onCancel, onSave }: {
  edit: Editing; state: { busy: boolean; error: string | null }; outages: boolean;
  onAdd: () => void; onReset: () => void; onCancel: () => void; onSave: () => void;
}) {
  const changed = !sameLayout(edit.layout, edit.before);
  const isDefault = sameLayout(edit.layout, defaultLayout(outages));
  return (
    <div class="ov-editbar" role="region" aria-label={t('Customize the Overview')} data-testid="ov-editbar">
      <div class="ov-editbar-text">
        <p>{edit.web.can_change
          ? <>{t('Drag a card by its handle to move it, within a column or to the other one.')} <span class="ov-keys">{t('With the keyboard: focus the handle and use the arrow keys.')}</span></>
          : t(edit.web.reason ?? 'Changes need WEB_BASIC_AUTH to be set.')}</p>
        {state.error && <p class="field-error" role="alert">{t(state.error)}</p>}
      </div>
      <div class="ov-editbar-actions">
        <button type="button" class="btn btn-small btn-primary" onClick={onAdd} data-testid="ov-add">+ {t('Add cards')}</button>
        <button type="button" class="btn btn-small" onClick={onReset} disabled={isDefault}>{t('Reset to default')}</button>
        <button type="button" class="btn btn-small" onClick={onCancel}>{t('Cancel')}</button>
        <button type="button" class="btn btn-small btn-primary" onClick={onSave} disabled={!edit.web.can_change || !changed || state.busy} data-testid="ov-save">
          {state.busy ? t('Saving…') : t('Save')}
        </button>
      </div>
    </div>
  );
}

export function AddCardsDialog({ layout, onAdd, onClose }: { layout: Layout; onAdd: (id: string) => void; onClose: () => void }) {
  const here = onPage(layout);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
  return (
    <div class="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }} data-testid="ov-add-dialog">
      <div class="modal ov-add" role="dialog" aria-modal="true" aria-labelledby="ov-add-title">
        <div class="modal-head">
          <h2 id="ov-add-title">{t('Add cards')}</h2>
          <button type="button" class="btn btn-small btn-quiet" ref={close} onClick={onClose} aria-label={t('Close')}>✕</button>
        </div>
        <div class="setting-hint">{t('A card goes to the end of its usual column; move it from there.')}</div>
        <ul class="ov-add-list">
          {[...CARDS.filter((c) => !here.has(c.id)), ...CARDS.filter((c) => here.has(c.id))].map((c) => {   // the ones to add first
            const on = here.has(c.id);
            return (
              <li key={c.id} class={on ? 'on' : ''}>
                <div><b>{t(c.title)}</b><span class="setting-hint">{t(c.desc)}</span></div>
                <button type="button" class="btn btn-small" disabled={on} onClick={() => onAdd(c.id)} data-testid={`ov-add-${c.id}`}>
                  {on ? t('On the page') : t('Add')}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

type Drag = { id: string; dx: number; dy: number; line: { left: number; top: number; width: number } | null };

/** Both columns. While editing, each card has a handle and ×; dropping shows a line where the card will go. */
export function LayoutColumns({ layout, editing, onChange, render }: {
  layout: Layout; editing: boolean; onChange: (l: Layout) => void; render: (id: string) => ComponentChildren;
}) {
  const cols = { left: useRef<HTMLDivElement>(null), right: useRef<HTMLDivElement>(null) };
  const [drag, setDrag] = useState<Drag | null>(null);
  const focusAfter = useRef<string | null>(null);
  useEffect(() => {
    if (!focusAfter.current) return;
    document.querySelector<HTMLElement>(`[data-card="${focusAfter.current}"] .ov-handle`)?.focus();
    focusAfter.current = null;
  });

  const locate = (x: number, y: number, id: string) => {
    const boxes = (['left', 'right'] as Col[]).map((col) => ({ col, el: cols[col].current!, r: cols[col].current!.getBoundingClientRect() }));
    const col = nearestColumn(boxes.map(({ col, r }) => ({ col, left: r.left, right: r.right, top: r.top, bottom: r.bottom })), x, y);
    const box = boxes.find((b) => b.col === col);
    if (!col || !box) return null;
    const cards = [...box.el.querySelectorAll<HTMLElement>(':scope > .ov-card')].filter((c) => c.dataset.card !== id).map((c) => c.getBoundingClientRect());
    const index = dropIndex(cards.map((r) => r.top + r.height / 2), y);
    const top = !cards.length ? box.r.top + 24 : index === 0 ? cards[0].top - 7
      : index === cards.length ? cards[cards.length - 1].bottom + 7 : (cards[index - 1].bottom + cards[index].top) / 2;
    return { col, index, line: { left: box.r.left, width: box.r.width, top } };
  };

  const startDrag = (e: PointerEvent, id: string) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY + scrollY;
    let px = e.clientX, py = e.clientY;
    let at: ReturnType<typeof locate> = null;
    const update = () => {
      at = locate(px, py, id);
      setDrag({ id, dx: px - sx, dy: py + scrollY - sy, line: at?.line ?? null });
    };
    const timer = window.setInterval(() => {         // scroll while the card is held near the top or bottom edge
      const dir = py > innerHeight - 72 ? 1 : py < 72 ? -1 : 0;
      if (dir) { scrollBy(0, dir * 14); update(); }
    }, 16);
    const move = (ev: PointerEvent) => { px = ev.clientX; py = ev.clientY; update(); };
    const finish = (drop: boolean) => {
      clearInterval(timer);
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', cancel);
      removeEventListener('keydown', esc);
      setDrag(null);
      if (drop && at) onChange(moveCard(layout, id, at.col, at.index));
    };
    const up = () => finish(true);
    const cancel = () => finish(false);
    const esc = (ev: KeyboardEvent) => { if (ev.key === 'Escape') finish(false); };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', cancel);
    addEventListener('keydown', esc);
    update();
  };

  const onKey = (e: KeyboardEvent, id: string) => {
    const next = stepCard(layout, id, e.key);
    if (!next) return;
    e.preventDefault();
    focusAfter.current = id;
    onChange(next);
  };

  const column = (col: Col) => (
    <div class={`ov-col ov-${col}`} ref={cols[col]} data-col={col}>
      {layout[col].map((id) => {
        const title = t(cardInfo(id)?.title ?? id);
        const dragging = drag?.id === id;
        return (
          <div key={id} class={`ov-card ${editing ? 'editing' : ''} ${dragging ? 'dragging' : ''}`} data-card={id}
               style={dragging ? { transform: `translate(${drag.dx}px, ${drag.dy}px)` } : undefined}>
            {editing && (
              <div class="ov-controls">
                <button type="button" class="ov-ctl ov-handle" aria-label={t('Move {card}', { card: title })} title={t('Drag to move; the arrow keys work too')}
                        onPointerDown={(e) => startDrag(e as unknown as PointerEvent, id)} onKeyDown={(e) => onKey(e as unknown as KeyboardEvent, id)}
                        data-testid={`ov-move-${id}`}>
                  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">{[6, 12, 18].flatMap((y) => [9, 15].map((x) => <circle key={`${x}${y}`} cx={x} cy={y} r="1.7" />))}</svg>
                  {t('Move')}
                </button>
                <button type="button" class="ov-ctl ov-remove" aria-label={t('Remove {card}', { card: title })} title={t('Remove from the Overview')}
                        onClick={() => onChange(removeCard(layout, id))} data-testid={`ov-remove-${id}`}>✕</button>
              </div>
            )}
            <div class="ov-body">{render(id)}</div>
          </div>
        );
      })}
      {editing && !layout[col].length && <div class="ov-empty">{t('Drop a card here')}</div>}
    </div>
  );

  return (
    <>
      <div class={`overview-grid ${editing ? 'editing' : ''}`}>
        {column('left')}
        {column('right')}
      </div>
      {drag?.line && <div class="ov-drop-line" style={{ left: `${drag.line.left}px`, top: `${drag.line.top - 2}px`, width: `${drag.line.width}px` }} />}
    </>
  );
}

