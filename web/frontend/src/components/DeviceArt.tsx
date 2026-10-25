import type { DeviceId } from '../lib/devices';

// Product-style drawings (light housings on the dark UI, like the Solis app), 120x120 viewBox.
const Defs = ({ id }: { id: string }) => (
  <defs>
    <linearGradient id={`${id}-body`} x1="0" x2="1" y1="0" y2="0">
      <stop offset="0" stop-color="#d9dce1" />
      <stop offset="0.45" stop-color="#ffffff" />
      <stop offset="1" stop-color="#c9ccd2" />
    </linearGradient>
    <linearGradient id={`${id}-side`} x1="0" x2="1">
      <stop offset="0" stop-color="#b9bcc3" />
      <stop offset="1" stop-color="#9ea2aa" />
    </linearGradient>
  </defs>
);

export function DeviceArt({ id, size = 96 }: { id: DeviceId; size?: number }) {
  const g = `art-${id}`;
  const body = `url(#${g}-body)`;
  const side = `url(#${g}-side)`;
  return (
    <svg viewBox="0 0 120 120" width={size} height={size} aria-hidden="true" class="device-art">
      <Defs id={g} />
      {id === 'inverter' && (
        <g>
          <path d="M84 14 L90 18 V100 L84 104 Z" fill={side} />
          <rect x="30" y="14" width="54" height="90" rx="4" fill={body} />
          <rect x="38" y="24" width="38" height="24" rx="2" fill="#2b3f57" />
          <rect x="41" y="27" width="32" height="5" fill="#5f8fc4" opacity="0.8" />
          <rect x="41" y="35" width="22" height="2.5" fill="#a9c3e0" opacity="0.7" />
          <rect x="41" y="40" width="28" height="2.5" fill="#a9c3e0" opacity="0.5" />
          <rect x="38" y="56" width="38" height="1.5" fill="#c3c6cc" />
          <circle cx="76" cy="92" r="2" fill="#8a8f98" />
          <path d="M36 104 v6 M44 104 v6 M52 104 v6 M62 104 v6 M70 104 v6 M78 104 v6" stroke="#4a4e55" stroke-width="3" />
        </g>
      )}
      {id === 'battery' && (
        <g>
          <path d="M86 12 L92 16 V104 L86 108 Z" fill={side} />
          <rect x="32" y="12" width="54" height="96" rx="4" fill={body} />
          <rect x="38" y="20" width="3" height="40" rx="1.5" fill="#6fd8f0" />
          <rect x="32" y="66" width="54" height="1.5" fill="#c3c6cc" />
          <rect x="32" y="104" width="54" height="4" rx="1" fill="#2c2f34" />
        </g>
      )}
      {id === 'logger' && (
        <g transform="rotate(14 60 60)">
          <rect x="52" y="8" width="16" height="12" rx="2" fill="#c9ccd2" />
          <path d="M52 11 h16 M52 14 h16 M52 17 h16" stroke="#9ea2aa" stroke-width="1" />
          <rect x="44" y="18" width="32" height="10" rx="3" fill="#e7e9ec" />
          <path d="M44 28 h32 l-3 76 a10 10 0 0 1 -10 9 h-6 a10 10 0 0 1 -10 -9 Z" fill={body} />
          <rect x="54" y="48" width="12" height="34" rx="6" fill="#d3d6db" />
          <circle cx="60" cy="56" r="2.2" fill="#b0b4ba" />
          <circle cx="60" cy="65" r="2.2" fill="#3fbf5a" />
          <circle cx="60" cy="74" r="2.2" fill="#e04848" />
        </g>
      )}
      {id === 'meter' && (
        <g>
          <path d="M88 12 L93 16 V104 L88 108 Z" fill={side} />
          <rect x="28" y="12" width="60" height="96" rx="4" fill={body} />
          <rect x="36" y="22" width="40" height="20" rx="2" fill="#5d6268" />
          <rect x="39" y="25" width="34" height="14" rx="1" fill="#9fa79f" />
          <path d="M42 30 h14 M42 35 h22" stroke="#4a524a" stroke-width="2" />
          <circle cx="80" cy="28" r="2" fill="#7a7f87" />
          <circle cx="80" cy="36" r="2" fill="#7a7f87" />
          <rect x="34" y="50" width="48" height="16" rx="2" fill="#e3e5e8" stroke="#c3c6cc" />
          <path d="M40 58 h8 M54 58 h8 M68 58 h8" stroke="#9ea2aa" stroke-width="1.5" />
          <rect x="28" y="72" width="60" height="1.5" fill="#c3c6cc" />
          <path d="M40 90 a6 6 0 0 1 12 0 M64 90 a6 6 0 0 1 12 0" stroke="#b5b9bf" stroke-width="2" fill="none" />
        </g>
      )}
    </svg>
  );
}
