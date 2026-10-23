import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';

import { describeError } from './client';

type Result<T> = { data?: T; error?: unknown; response?: Response };

type Options = {
  /** What to run when the screen comes back into view, or the app back to the
   * front, instead of a quiet reload. */
  onRefocus?: () => void;
  /** Also reload quietly this often (ms) while the screen is in view. */
  every?: number;
};

/** Load one API read and keep it current: again, quietly, whenever the screen
 * comes back into view or the app back to the front, and with a spinner on
 * pull-to-refresh. A screen left open is never a stale picture of the
 * accounts. `load` should be stable for the screen (useCallback). */
export function useRequest<T>(load: () => Promise<Result<T>>, options: Options = {}) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const live = useRef(true);
  // Only the newest load may land: an older answer arriving late is dropped.
  const newest = useRef(0);

  const run = useCallback(
    async (pulled: boolean) => {
      const mine = ++newest.current;
      setLoading(true);
      if (pulled) setRefreshing(true);
      try {
        const result = await load();
        if (!live.current || mine !== newest.current) return;
        if (result.data !== undefined) {
          setData(result.data);
          setError(null);
          setReceivedAt(Date.now());
        } else {
          setError(describeError(result.error, result.response));
        }
      } catch (thrown) {
        if (live.current && mine === newest.current) setError(describeError(undefined, undefined, thrown));
      } finally {
        if (live.current && mine === newest.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [load],
  );

  /** Pull-to-refresh: shows the spinner. */
  const reload = useCallback(() => run(true), [run]);
  /** Automatic: quiet. */
  const refetch = useCallback(() => run(false), [run]);

  useEffect(() => {
    live.current = true;
    run(false);
    return () => {
      live.current = false;
    };
  }, [run]);

  // Held in a ref so the focus effect below never re-runs merely because a
  // screen re-created its callback.
  const refocus = useRef<() => void>(refetch);
  useEffect(() => {
    refocus.current = options.onRefocus ?? refetch;
  });

  const every = options.every;
  const shown = useRef(false);
  useFocusEffect(
    useCallback(() => {
      // The first time in view is the load the mount already started.
      if (shown.current) refocus.current();
      shown.current = true;
      const subscription = AppState.addEventListener('change', (next) => {
        if (next === 'active') refocus.current();
      });
      // Not while the app is in the background: nobody is looking.
      const timer = every
        ? setInterval(() => AppState.currentState !== 'background' && refocus.current(), every)
        : undefined;
      return () => {
        subscription.remove();
        clearInterval(timer);
      };
    }, [every]),
  );

  return { data, error, loading, refreshing, receivedAt, reload, refetch, setData };
}

/** The time now, read again every `ms` while the screen is in view, whenever
 * the app comes back to the front, and at `wakeAt` (a moment something on
 * screen goes out of date): for judging how old what is on screen has become.
 * (Date.now() during render would be memoised away.) */
export function useNow(ms: number, wakeAt: number | null = null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (wakeAt === null) return undefined;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, wakeAt - Date.now()) + 1);
    return () => clearTimeout(timer);
  }, [wakeAt]);
  useFocusEffect(
    useCallback(() => {
      setNow(Date.now());
      const timer = setInterval(() => setNow(Date.now()), ms);
      const subscription = AppState.addEventListener('change', (next) => {
        if (next === 'active') setNow(Date.now());
      });
      return () => {
        clearInterval(timer);
        subscription.remove();
      };
    }, [ms]),
  );
  return now;
}
