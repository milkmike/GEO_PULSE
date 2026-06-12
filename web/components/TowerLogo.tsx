/** The Massaraksh transmitter tower: idle it breathes, on hover (via parent
 *  `.group`) the head spins up and the signal waves go to combat cadence. */
export default function TowerLogo() {
  return (
    <svg
      width="22"
      height="26"
      viewBox="0 0 24 28"
      fill="none"
      aria-hidden="true"
      className="relative top-[1px] shrink-0 overflow-visible"
    >
      {/* signal waves */}
      <g stroke="var(--color-ru-blue)" strokeWidth="1">
        <circle className="tower-wave" cx="12" cy="5" r="4.5" />
        <circle className="tower-wave tower-wave-2" cx="12" cy="5" r="4.5" />
        <circle className="tower-wave tower-wave-3" cx="12" cy="5" r="4.5" />
      </g>

      {/* lattice mast */}
      <g stroke="var(--color-ru-white)" strokeWidth="1.1" strokeLinecap="round" opacity="0.85">
        <path d="M8.5 26 L12 8 L15.5 26" />
        <path d="M9.6 21 L14.4 21" strokeWidth="0.8" />
        <path d="M10.4 16.5 L13.6 16.5" strokeWidth="0.8" />
        <path d="M9.6 21 L13.6 16.5 M14.4 21 L10.4 16.5" strokeWidth="0.6" opacity="0.7" />
        <path d="M10.4 16.5 L13 12.5 M13.6 16.5 L11 12.5" strokeWidth="0.6" opacity="0.7" />
      </g>

      {/* rotating head */}
      <g className="tower-head" stroke="var(--color-ru-white)" strokeWidth="1.1" strokeLinecap="round">
        <path d="M9.5 9.5 L14.5 7.5" />
        <path d="M12 8.5 L12 5.8" strokeWidth="0.9" />
      </g>

      {/* beacon */}
      <circle className="tower-beacon" cx="12" cy="5" r="1.6" fill="var(--color-ru-red)" />
    </svg>
  );
}
