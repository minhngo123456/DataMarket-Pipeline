import { useCallback, useEffect, useState } from 'react';
import { mockFallbackEnabled } from '../config.js';
import { marketDataApi } from '../services/marketDataApi.js';

const MOCK_USER = {
  user_id: 'u-001',
  tenant_id: 't-001',
  role: import.meta.env.VITE_MOCK_ROLE || 'admin',
  tenant: {
    name: 'Shop ABC',
    status: 'active',
    product_count: 0,
    max_skus: 100,
    max_sources: 3,
  },
};

export function useUser() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isMock, setIsMock] = useState(false);
  const [error, setError] = useState(null);
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => {
    setLoading(true);
    setAttempt((currentAttempt) => currentAttempt + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    marketDataApi
      .getCurrentUser({ signal: controller.signal })
      .then((currentUser) => {
        setUser(currentUser);
        setIsMock(false);
        setError(null);
      })
      .catch((requestError) => {
        if (controller.signal.aborted) {
          return;
        }

        if (mockFallbackEnabled) {
          setUser(MOCK_USER);
          setIsMock(true);
          setError(null);
        } else {
          setUser(null);
          setIsMock(false);
          setError(requestError);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [attempt]);

  return { user, loading, isMock, error, retry };
}
