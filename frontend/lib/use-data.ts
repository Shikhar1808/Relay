"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

/** GETs `path` on mount. `reload` refetches after a change. A null path loads nothing. */
export function useData<T>(path: string | null) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();

  const reload = useCallback(async () => {
    if (path === null) return;
    try {
      setData(await api<T>(path));
      setError(undefined);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [path]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- state is set only after the fetch resolves
    void reload();
  }, [reload]);

  return { data, error, reload };
}
