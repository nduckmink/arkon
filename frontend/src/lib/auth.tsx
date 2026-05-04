"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import { api, setToken, clearToken, ApiError } from "@/lib/api";

export type User = {
  id: string;
  name: string;
  email: string;
  role: "admin" | "employee";
  department_id: string;
  department_name: string;
  permissions: string[];
};

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
  hasPermission: (perm: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api<{
        id: string;
        name: string;
        email: string;
        role: "admin" | "employee";
        department_id: string;
        department_name: string;
        permissions: string[];
      }>("/api/auth/me");
      setUser(data);
    } catch (err) {
      setUser(null);
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        clearToken();
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const token = localStorage.getItem("arkon_token");
    if (token) {
      refresh();
    } else {
      setLoading(false);
    }
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const data = await api<{ access_token: string; user: User }>(
      "/api/auth/login",
      {
        method: "POST",
        body: { email, password },
      }
    );
    setToken(data.access_token);
    setUser(data.user);
  };

  const logout = () => {
    clearToken();
    setUser(null);
  };

  const hasPermission = useCallback(
    (perm: string) => {
      if (!user) return false;
      if (user.role === "admin") return true;
      return user.permissions?.includes(perm) ?? false;
    },
    [user]
  );

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
