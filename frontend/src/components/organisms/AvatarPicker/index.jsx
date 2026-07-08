const CATEGORY_LABELS = {
  person: "People",
  tech: "Tech",
  animal: "Animals",
  creature: "Creatures"
};

const CATEGORY_ORDER = ["person", "tech", "animal", "creature"];

function groupByCategory(avatars) {
  const groups = new Map();
  for (const avatar of avatars) {
    const key = avatar.category || "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(avatar);
  }
  return [...groups.entries()].sort((a, b) => {
    const indexA = CATEGORY_ORDER.indexOf(a[0]);
    const indexB = CATEGORY_ORDER.indexOf(b[0]);
    return (indexA === -1 ? CATEGORY_ORDER.length : indexA) - (indexB === -1 ? CATEGORY_ORDER.length : indexB);
  });
}

export function AvatarPicker({ avatars = [], value = "", onSelect }) {
  const groups = groupByCategory(avatars);

  return (
    <div className="avatar-picker" aria-label="Avatar picker">
      {groups.map(([category, items]) => (
        <div key={category} className="avatar-picker-group">
          <div className="mono avatar-picker-group-label">{CATEGORY_LABELS[category] || category}</div>
          <div className="avatar-picker-grid">
            {items.map((avatar) => {
              const selected = avatar.slug === value;
              return (
                <button
                  key={avatar.slug}
                  type="button"
                  className={`avatar-tile${selected ? " avatar-tile-selected" : ""}`}
                  aria-pressed={selected}
                  title={avatar.label}
                  onClick={() => onSelect(avatar.slug)}
                >
                  <img src={`/static/avatars/${avatar.slug}.png`} alt={avatar.label} />
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
