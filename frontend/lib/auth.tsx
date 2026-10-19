"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { api, SIGNED_OUT_EVENT, type TokenResponse, type User } from "./api";

type Auth = {
  /** undefined while the API is being asked who is signed in, null when signed out. */
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
    // The session cookie cannot be seen from here, so the API is asked who is signed in.
    api<{ user: User | null }>("/auth/session")
      .then((session) => setUser(session.user))
      .catch(() => setUser(null));

    const onSignedOut = () => setUser(null);
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, []);

  const adopt = useCallback((session: TokenResponse) => setUser(session.user), []);

  const login = useCallback(
    async (email: string, password: string) => {
      adopt(await api<TokenResponse>("/auth/login", { body: { email, password } }));
    },
    [adopt],
  );

  const logout = useCallback(() => {
    // The API removes the cookie and stops the token in it from working. If it cannot be reached
    // this page still forgets the user, but the session lives on and a reload signs back in.
    void api("/auth/logout", { method: "POST" })
      .catch(() => undefined)
      .then(() => setUser(null));
  }, []);

  return <AuthContext value={{ user, login, adopt, logout }}>{children}</AuthContext>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
