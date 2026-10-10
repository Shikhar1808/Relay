"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, getToken, setToken, SIGNED_OUT_EVENT, type TokenResponse, type User } from "./api";

type Auth = {
  /** undefined while the saved session is being checked, null when signed out. */
  user: User | null | undefined;
  login: (email: string, password: string) => Promise<void>;
  /** Used after an emailed link sets a password: the API answers with a session. */
  adopt: (session: TokenResponse) => void;
  logout: () => void;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    const restore = getToken() ? api<User>("/auth/me") : Promise.resolve(null);
    restore.then(setUser).catch(() => {
      setToken(null);
      setUser(null);
    });

    const onSignedOut = () => setUser(null);
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, []);

  const adopt = useCallback((session: TokenResponse) => {
    setToken(session.access_token);
    setUser(session.user);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      adopt(await api<TokenResponse>("/auth/login", { body: { email, password } }));
    },
    [adopt],
  );

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  return <AuthContext value={{ user, login, adopt, logout }}>{children}</AuthContext>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
