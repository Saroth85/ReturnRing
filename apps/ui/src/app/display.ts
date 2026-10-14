// Theme (light, dark or the system's) and presenter mode (docs/ui-design.md 3.4 and 4.2).
// `?theme=light` and `?present=1` on any URL set them; both apply to <html>.
import { useEffect } from 'react';
import { createStore, safeStorage, useStore } from '@/api/store';

export type ThemeSetting = 'system' | 'light' | 'dark';
const THEME_KEY = 'returnring.theme';
const PRESENT_KEY = 'returnring.present';

function fromUrl(name: string): string | null {
  if (typeof window === 'undefined') return null;
  return new URLSearchParams(window.location.search).get(name);
}

function initialTheme(): ThemeSetting {
  const url = fromUrl('theme');
  if (url === 'light' || url === 'dark' || url === 'system') {
    safeStorage.set('local', THEME_KEY, url);
    return url;
  }
  const stored = safeStorage.get('local', THEME_KEY);
  return stored === 'light' || stored === 'dark' ? stored : 'system';
}

function initialPresent(): boolean {
  const url = fromUrl('present');
  if (url === '1' || url === '0') {
    safeStorage.set('session', PRESENT_KEY, url === '1' ? '1' : null);
    return url === '1';
  }
  return safeStorage.get('session', PRESENT_KEY) === '1';
}

export const themeStore = createStore<ThemeSetting>(initialTheme());
export const presentStore = createStore<boolean>(initialPresent());

export function setTheme(theme: ThemeSetting): void {
  safeStorage.set('local', THEME_KEY, theme === 'system' ? null : theme);
  themeStore.set(theme);
}

export function setPresent(on: boolean): void {
  safeStorage.set('session', PRESENT_KEY, on ? '1' : null);
  presentStore.set(on);
}

export const useTheme = () => useStore(themeStore);
export const usePresent = () => useStore(presentStore);

/** Applies the theme and presenter mode to <html>. */
export function useDisplaySettings(): void {
  const theme = useTheme();
  const present = usePresent();
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    const root = document.documentElement;
    if (present) root.dataset.present = '1';
    else delete root.dataset.present;
  }, [present]);
}
