import { useTheme } from '../hooks/useTheme';

export default function ThemeSwitcher() {
  const { theme, setTheme, themes } = useTheme();

  return (
    <div className="flex items-center gap-0.5 bg-[var(--color-surface)] glass-surface border border-[var(--color-border)] rounded-full p-0.5">
      {themes.map(t => (
        <button
          key={t.name}
          type="button"
          onClick={() => setTheme(t.name)}
          className={`min-h-[40px] rounded-full px-3 text-[11px] transition-colors duration-200 ${
            theme === t.name
              ? 'bg-[var(--color-primary-bg)] text-[var(--color-primary-text)] shadow-sm'
              : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
