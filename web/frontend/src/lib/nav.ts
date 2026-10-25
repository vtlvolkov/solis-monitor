/** Switch the app's view (and optional extra URL params) without a page load: App listens for popstate. */
export function goTo(view: string, params: Record<string, string> = {}): void {
  const p = new URLSearchParams();
  const keep = new URLSearchParams(location.search);
  for (const k of ['lang', 'transport']) if (keep.get(k)) p.set(k, keep.get(k)!);   // sticky debug/language params
  if (view !== 'overview') p.set('view', view);
  for (const [k, v] of Object.entries(params)) p.set(k, v);
  const qs = p.toString();
  history.pushState(null, '', qs ? `?${qs}` : location.pathname);
  dispatchEvent(new PopStateEvent('popstate'));
  scrollTo(0, 0);
}
