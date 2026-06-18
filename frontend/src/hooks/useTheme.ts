import { useState, useEffect, useCallback } from 'react';
import type { ThemeName } from '../theme';
import { THEMES } from '../theme';

const STORAGE_KEY = 'theme';

function getInitialTheme(): ThemeName {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'glass' || saved === 'indigo') return saved;
  } catch { /* ignore */ }
  return 'light';
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemeName>(getInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((t: ThemeName) => {
    setThemeState(t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch { /* ignore */ }
  }, []);

  return { theme, setTheme, themes: THEMES };
}
