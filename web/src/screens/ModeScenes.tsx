// ONB-4: живі сценки до карток «Шукаю житло» / «Здаю житло» (ілюстрації — оригінальний
// маскот Домовика, CSS-анімації; при «зменшеному русі» стоять нерухомо).

type Palette = { coat: string; band: string; pom: string; beardLine: string };

export const GREEN: Palette = { coat: "#1E4D3B", band: "#163A2C", pom: "#F2A93B", beardLine: "#CBD6CF" };
export const HONEY: Palette = { coat: "#E2B45A", band: "#8B6522", pom: "#1E4D3B", beardLine: "#E5D7B4" };

/** Домовик у координатах 200×200 (як assets/brand/mascot.svg), без тіні й без руки. */
function Gnome({ p }: { p: Palette }) {
  return (
    <>
      <path d="M64 146 Q60 184 72 184 L128 184 Q140 184 136 146 Z" fill={p.coat} />
      <ellipse className="foot foot-l" cx="82" cy="186" rx="13" ry="6" fill="#3B2A20" />
      <ellipse className="foot foot-r" cx="118" cy="186" rx="13" ry="6" fill="#3B2A20" />
      <path d="M50 94 Q44 152 100 172 Q156 152 150 94 Z" fill="#FFFFFF" stroke={p.beardLine} strokeWidth="2" />
      <path d="M46 96 C 56 58, 80 26, 120 12 C 132 8, 140 16, 132 22 C 120 30, 116 56, 154 96 Z" fill={p.coat} />
      <circle cx="134" cy="16" r="7" fill={p.pom} />
      <rect x="42" y="86" width="116" height="16" rx="8" fill={p.band} />
      <ellipse cx="78" cy="110" rx="6" ry="4" fill="#F4B8A8" />
      <ellipse cx="122" cy="110" rx="6" ry="4" fill="#F4B8A8" />
      <circle cx="100" cy="112" r="12" fill="#E8907A" />
    </>
  );
}

function Tree({ x, h, dark = false }: { x: number; h: number; dark?: boolean }) {
  return (
    <g>
      <rect x={x - 2} y={118 - 8} width="4" height="10" fill="#7A5A3C" />
      <path d={`M${x} ${118 - h} L${x + h * 0.38} ${112} L${x - h * 0.38} ${112} Z`} fill={dark ? "#1E4D3B" : "#2E6B53"} />
    </g>
  );
}

const FOREST = [8, 30, 46, 70, 88, 112, 128, 150];

/** Орендар: з клунком на палиці йде лісовою стежкою до міста. */
export function TenantScene() {
  return (
    <svg className="mode-scene scene-walk" viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="sky-t" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#DDEBE2" />
          <stop offset="1" stopColor="#F6F4EE" />
        </linearGradient>
      </defs>
      <rect width="320" height="180" fill="url(#sky-t)" />
      <circle cx="270" cy="34" r="16" fill="#F2A93B" opacity="0.35" />
      {/* Місто на обрії */}
      <g fill="#B8CCBF">
        <rect x="236" y="84" width="14" height="30" />
        <rect x="252" y="74" width="12" height="40" />
        <path d="M266 114 V64 L272 50 L278 64 V114 Z" />
        <rect x="282" y="80" width="16" height="34" />
        <rect x="300" y="90" width="14" height="24" />
      </g>
      <g fill="#F6F4EE" opacity="0.9">
        <rect x="240" y="90" width="3" height="4" />
        <rect x="255" y="80" width="3" height="4" />
        <rect x="286" y="86" width="3" height="4" />
        <rect x="292" y="96" width="3" height="4" />
      </g>
      <path d="M0 120 Q80 98 160 112 T320 112 V180 H0 Z" fill="#CFE3D5" />
      {/* Ліс пливе назад — Домовик іде вперед */}
      <g className="forest">
        {[0, 170].map((dx) => (
          <g key={dx} transform={`translate(${dx} 0)`}>
            {FOREST.map((x, i) => (
              <Tree key={x} x={x} h={i % 3 === 0 ? 34 : i % 3 === 1 ? 26 : 30} dark={i % 2 === 0} />
            ))}
          </g>
        ))}
      </g>
      {/* Стежка до міста */}
      <path d="M40 180 Q120 150 170 136 T262 114 L272 116 Q200 140 190 148 T120 180 Z" fill="#EADFC6" />
      <g className="walker" transform="translate(96 78) scale(0.48)">
        <g className="bob">
          <line x1="134" y1="150" x2="160" y2="118" stroke={GREEN.coat} strokeWidth="11" strokeLinecap="round" />
          <Gnome p={GREEN} />
          {/* Палиця з клунком */}
          <line x1="150" y1="132" x2="200" y2="44" stroke="#7A5A3C" strokeWidth="6" strokeLinecap="round" />
          <g className="bindle">
            <circle cx="206" cy="58" r="20" fill="#C9553F" />
            <circle cx="198" cy="54" r="3" fill="#F6F4EE" />
            <circle cx="212" cy="64" r="3" fill="#F6F4EE" />
            <circle cx="210" cy="48" r="2.5" fill="#F6F4EE" />
            <path d="M196 42 L206 38 L214 44" fill="none" stroke="#C9553F" strokeWidth="5" strokeLinecap="round" />
          </g>
          <circle cx="160" cy="118" r="9" fill="#F2C9A0" />
        </g>
      </g>
    </svg>
  );
}

/** Власник: жовтий Домовик біля свого будиночка — з комина дим, у вікнах світло. */
export function LandlordScene() {
  return (
    <svg className="mode-scene scene-home" viewBox="0 0 320 180" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <linearGradient id="sky-l" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#F8EBCB" />
          <stop offset="1" stopColor="#F6F4EE" />
        </linearGradient>
      </defs>
      <rect width="320" height="180" fill="url(#sky-l)" />
      <circle cx="54" cy="36" r="13" fill="#FFFFFF" opacity="0.7" />
      <path d="M0 150 Q160 132 320 150 V180 H0 Z" fill="#D9E6CC" />
      {/* Дим з комина */}
      <g className="smoke" fill="#FFFFFF">
        <circle className="puff p1" cx="252" cy="34" r="6" />
        <circle className="puff p2" cx="252" cy="34" r="5" />
        <circle className="puff p3" cx="252" cy="34" r="7" />
      </g>
      <rect x="244" y="36" width="16" height="30" fill="#8B6522" />
      {/* Будиночок */}
      <rect x="150" y="76" width="124" height="80" rx="4" fill="#FFF8EA" stroke="#E5D7B4" strokeWidth="2" />
      <path d="M138 80 L212 30 L286 80 Z" fill="#B5643C" />
      <path d="M138 80 L212 30 L286 80" fill="none" stroke="#8E4A2A" strokeWidth="4" strokeLinejoin="round" />
      <rect className="win w1" x="162" y="96" width="26" height="24" rx="3" stroke="#B5A57E" strokeWidth="2" />
      <rect className="win w2" x="236" y="96" width="26" height="24" rx="3" stroke="#B5A57E" strokeWidth="2" />
      <path d="M175 96 V120 M162 108 H188 M249 96 V120 M236 108 H262" stroke="#B5A57E" strokeWidth="2" />
      <path d="M199 156 V126 Q212 112 225 126 V156 Z" fill="#8B6522" />
      <circle cx="220" cy="142" r="2" fill="#F2C9A0" />
      {/* Квіти біля дому */}
      <g>
        <circle cx="160" cy="158" r="4" fill="#E8907A" />
        <circle cx="170" cy="160" r="3.5" fill="#F2A93B" />
        <circle cx="262" cy="158" r="4" fill="#F4B8A8" />
        <circle cx="272" cy="160" r="3.5" fill="#E8907A" />
      </g>
      <g className="host" transform="translate(28 66) scale(0.5)">
        <g className="bob">
          <ellipse cx="100" cy="190" rx="58" ry="6" fill="#000" opacity="0.06" />
          <g className="wave-arm">
            <line x1="134" y1="150" x2="160" y2="112" stroke={HONEY.coat} strokeWidth="11" strokeLinecap="round" />
            <circle cx="163" cy="106" r="9" fill="#F2C9A0" />
          </g>
          <Gnome p={HONEY} />
          <path className="wave-line" d="M174 92 Q180 98 178 106" fill="none" stroke={HONEY.band} strokeWidth="3" strokeLinecap="round" />
          <path className="wave-line l2" d="M168 86 Q176 88 180 94" fill="none" stroke={HONEY.band} strokeWidth="3" strokeLinecap="round" />
        </g>
      </g>
    </svg>
  );
}
