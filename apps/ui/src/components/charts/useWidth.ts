import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * The content width of an element, kept up to date with a ResizeObserver. Charts get an explicit
 * width, so they draw nothing (and warn about nothing) until the layout has given them room.
 */
export function useWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node) return;
    setWidth(Math.floor(node.getBoundingClientRect().width));
    if (typeof ResizeObserver === 'undefined') return;
    observer.current = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(Math.floor(entry.contentRect.width));
    });
    observer.current.observe(node);
  }, []);

  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, width];
}
