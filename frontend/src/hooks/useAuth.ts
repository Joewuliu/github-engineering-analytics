import { useCallback, useEffect, useState } from "react";
import { getCurrentUser } from "../api/client";
import type { User } from "../api/types";

export interface AuthState {
  user: User | null;
  /** True only during the initial /auth/me check -- lets App.tsx show a
   * brief loading state instead of flashing the landing page first. */
  loading: boolean;
  refresh: () => Promise<void>;
  clear: () => void;
}

/** Backs the whole app's auth state. A 401 here is the normal,
 * expected "not signed in yet" case -- never treated as an error. */
export function useAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setUser(await getCurrentUser());
    } catch {
      // 401 (not signed in) and any other failure both just mean "show the
      // landing page" -- there is nothing else useful to do here.
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const clear = useCallback(() => setUser(null), []);

  return { user, loading, refresh, clear };
}
