interface Props {
  companies: string[];
  selected: string;
  onSelect: (name: string) => void;
  variant?: 'floating' | 'sidebar';
}

export default function WatchlistPanel({ companies, selected, onSelect, variant = 'sidebar' }: Props) {
  if (companies.length === 0) {
    if (variant === 'floating') return null;
    return (
      <div className="px-4 py-3 text-[11px] text-gray-300">暂无监控企业</div>
    );
  }

  const list = (
    <div className="space-y-0.5">
      {companies.map(c => (
        <button
          key={c}
          onClick={() => onSelect(c)}
          className={`w-full text-left text-sm rounded-lg px-3 py-2 transition-colors truncate ${
            selected === c
              ? 'bg-[var(--color-primary-bg)] text-white'
              : 'text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]'
          }`}
        >
          {c}
        </button>
      ))}
    </div>
  );

  if (variant === 'floating') {
    return (
      <div className="hidden xl:block fixed top-1/2 -translate-y-1/2 z-10"
        style={{ left: `max(24px, calc((100vw - 672px) / 2 - 184px))` }}>
        <div className="w-40 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-2xl shadow-md overflow-hidden">
          <h3 className="text-[11px] font-medium text-gray-400 px-3 pt-3 pb-2">监控清单</h3>
          <div className="max-h-[50vh] overflow-auto px-1.5 pb-1.5">{list}</div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 className="text-[11px] font-medium text-gray-400 uppercase tracking-wide px-3 pt-3 pb-2">监控清单</h3>
      <div className="px-1.5 pb-1.5">{list}</div>
    </div>
  );
}
