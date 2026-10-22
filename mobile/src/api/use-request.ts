import { useCallback, useEffect, useRef, useState } from 'react';

import { describeError } from './client';

type Result<T> = { data?: T; error?: unknown; response?: Response };

/** Load one API read, with a reload for pull-to-refresh. `load` should be
 * stable for the screen (wrap it in useCallback with its inputs). */
export function useRequest<T>(load: () => Promise<Result<T>>) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const live = useRef(true);

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const result = await load();
      if (!live.current) return;
      if (result.data !== undefined) {
        setData(result.data);
        setError(null);
      } else {
        setError(describeError(result.error, result.response));
      }
    } catch {
      if (live.current) setError(describeError(undefined));
    } finally {
      if (live.current) setLoading(false);
    }
  }, [load]);

  useEffect(() => {
    live.current = true;
    run();
    return () => {
      live.current = false;
    };
  }, [run]);

  return { data, error, loading, reload: run };
}
