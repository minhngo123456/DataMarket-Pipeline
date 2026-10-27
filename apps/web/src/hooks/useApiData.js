import { useCallback, useEffect, useState } from 'react';
import { mockFallbackEnabled } from '../config.js';

/**
 * Loads one API resource. When the API is unreachable and sample data is
 * allowed (local development), the sample is returned with isMock=true so the
 * page can label it.
 */
export function useApiData(load, sample, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null, isMock: false });
  const [attempt, setAttempt] = useState(0);

  const reload = useCallback(() => setAttempt((value) => value + 1), []);
  const setData = useCallback(
    (update) => setState((current) => ({
      ...current,
      data: typeof update === 'function' ? update(current.data) : update,
    })),
    [],
  );

  useEffect(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true }));
    load()
      .then((data) => active && setState({ data, loading: false, error: null, isMock: false }))
      .catch((error) => {
        if (!active) return;
        if (mockFallbackEnabled && sample !== undefined) {
          try {
            const data = typeof sample === 'function' ? sample() : sample;
            setState({ data, loading: false, error: null, isMock: true });
            return;
          } catch {
            // No sample for this request: report the original API error.
          }
        }
        setState({ data: null, loading: false, error, isMock: false });
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, ...deps]);

  return { ...state, reload, setData };
}

/** Error message from the backend's { error: { code, message } } body. */
export function apiErrorMessage(error, fallback = 'Không kết nối được API.') {
  return error?.response?.data?.error?.message || error?.message || fallback;
}
