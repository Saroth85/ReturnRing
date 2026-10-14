// A tiny external store for React (useSyncExternalStore), used by the API layer's meta and
// scenario stores and by the shell. No dependency.
import { useSyncExternalStore } from 'react';

export interface Store<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next) {
      const value = typeof next === 'function' ? (next as (prev: T) => T)(state) : next;
      if (Object.is(value, state)) return;
      state = value;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

/** sessionStorage and localStorage can throw (private windows, blocked storage): never fail. */
export const safeStorage = {
  get(kind: 'local' | 'session', key: string): string | null {
    try {
      return (kind === 'local' ? window.localStorage : window.sessionStorage).getItem(key);
    } catch {
      return null;
    }
  },
  set(kind: 'local' | 'session', key: string, value: string | null): void {
    try {
      const storage = kind === 'local' ? window.localStorage : window.sessionStorage;
      if (value === null) storage.removeItem(key);
      else storage.setItem(key, value);
    } catch {
      // storage unavailable: the value lives for this page only
    }
  },
};
