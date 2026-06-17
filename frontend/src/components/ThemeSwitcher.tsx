import { useTheme } from '../hooks/useTheme';

export default function ThemeSwitcher() {
  const { theme, setTheme, themes } = useTheme();

  return (
    <div className="flex items-center gap-1">
      {themes.map(t => (
        <button
          key={t.name}
          onClick={() => setTheme(t.name)}
          className={`text-[11px] px-2.5 py-1 rounded-full transition-colors ${
            theme === t.name
              ? 'bg-[var(--color-primary-bg)] text-[var(--color-primary-text)]'
              : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
