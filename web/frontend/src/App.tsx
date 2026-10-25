import { useEffect, useState } from 'preact/hooks';
import { NavBar, type View } from './components/common';
import { useLang } from './lib/i18n';
import { useLive, useNow } from './lib/live';
import { Charts } from './pages/Charts';
import { CostHistory } from './pages/CostHistory';
import { Devices } from './pages/Devices';
import { Events } from './pages/Events';
import { parseView as readDevice, type DeviceId } from './lib/devices';
import { Overview } from './pages/Overview';
import { Settings } from './pages/Settings';
import { Tv } from './pages/Tv';

const readView = (): View => {
  const v = new URLSearchParams(location.search).get('view');
  return v === 'charts' || v === 'settings' || v === 'devices' || v === 'events' || v === 'tv' || v === 'cost' ? v : 'overview';
};

export function App() {
  useLang();                       // re-render everything when the language changes
  const live = useLive();
  const now = useNow(1000);
  const [view, setView] = useState<View>(readView);
  const [device, setDevice] = useState<DeviceId | null>(() => readDevice(location.search));

  useEffect(() => {
    const onPop = () => { setView(readView()); setDevice(readDevice(location.search)); };
    addEventListener('popstate', onPop);
    return () => removeEventListener('popstate', onPop);
  }, []);

  const go = (v: View, dev: DeviceId | null = null) => {
    if (v === view && dev === device) return;
    const p = new URLSearchParams(location.search);
    if (v === 'overview') p.delete('view'); else p.set('view', v);
    if (dev) p.set('device', dev); else p.delete('device');
    if (v !== 'cost') { p.delete('month'); p.delete('day'); }          // they belong to the cost history page
    const qs = p.toString();
    history.pushState(null, '', qs ? `?${qs}` : location.pathname);
    setView(v);
    setDevice(dev);
    scrollTo(0, 0);
  };

  if (view === 'tv') return <Tv live={live} now={now} />;   // wall display: no nav, no scrolling

  return (
    <>
      <main class="app">
        {view === 'charts' ? <Charts live={live} now={now} />
          : view === 'settings' ? <Settings live={live} now={now} />
          : view === 'events' ? <Events live={live} now={now} />
          : view === 'cost' ? <CostHistory live={live} now={now} />
          : view === 'devices' ? <Devices live={live} now={now} device={device} onOpen={(d) => go('devices', d)} />
          : <Overview live={live} now={now} />}
      </main>
      <NavBar view={view} onChange={(v) => go(v)} />
    </>
  );
}
