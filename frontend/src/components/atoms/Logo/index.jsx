// Agent Gateway brand mark. Decorative by default (always paired with the
// wordmark), so it is aria-hidden. Pass reversed for dark backgrounds.
export function Logo({ reversed = false, className = "" }) {
  const ink = reversed ? "#ffffff" : "#0A0A0A";
  const green = "#22C55E";
  return (
    <svg
      viewBox="0 0 152 100"
      className={["logo", className].filter(Boolean).join(" ")}
      aria-hidden="true"
      focusable="false"
    >
      <rect x="0" y="45" width="9" height="10" fill={ink} />
      <rect x="15" y="45" width="9" height="10" fill={ink} />
      <path d="M56 16 H98 V84 H56" fill="none" stroke={ink} strokeWidth="13" />
      <rect x="30" y="44" width="42" height="12" fill={green} />
      <polygon points="66,31 94,50 66,69" fill={green} />
      <rect x="110" y="45" width="9" height="10" fill={green} />
      <rect x="125" y="45" width="9" height="10" fill={ink} />
      <rect x="140" y="45" width="9" height="10" fill={ink} />
    </svg>
  );
}
