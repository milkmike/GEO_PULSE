"use client";

import { useState, useEffect, createContext, useContext } from "react";

const AUTH_KEY = "gp_v2_auth";
const AUTH_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days

// Password hash (SHA-256 of the password)
// To change password: run in browser console:
// crypto.subtle.digest('SHA-256', new TextEncoder().encode('YOUR_PASSWORD')).then(b => console.log(Array.from(new Uint8Array(b)).map(x=>x.toString(16).padStart(2,'0')).join('')))
const PASSWORD_HASH = "ff4445978f3c62e10244a79261cc3115a2130e109a94986a59c2a8943bad755f";

async function hashPassword(password: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(password);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function getStoredAuth(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const stored = localStorage.getItem(AUTH_KEY);
    if (!stored) return false;
    const { expires } = JSON.parse(stored);
    if (Date.now() > expires) {
      localStorage.removeItem(AUTH_KEY);
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function setStoredAuth() {
  localStorage.setItem(
    AUTH_KEY,
    JSON.stringify({ expires: Date.now() + AUTH_TTL })
  );
}

const AuthContext = createContext<{ logout: () => void }>({ logout: () => {} });

export function useV2Auth() {
  return useContext(AuthContext);
}

export default function V2AuthGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState(false);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    setAuthed(getStoredAuth());
  }, []);

  const handleLogin = async () => {
    setChecking(true);
    setError(false);
    const hash = await hashPassword(password);
    if (hash === PASSWORD_HASH) {
      setStoredAuth();
      setAuthed(true);
    } else {
      setError(true);
    }
    setChecking(false);
  };

  const logout = () => {
    localStorage.removeItem(AUTH_KEY);
    setAuthed(false);
    setPassword("");
  };

  // Loading state
  if (authed === null) {
    return null;
  }

  // Login screen
  if (!authed) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-card p-6">
          <div className="text-center">
            <div className="text-2xl font-bold">
              <span className="text-blue-500">GEO</span>
              <span className="text-white">PULSE</span>
              <span className="ml-2 rounded bg-blue-500/20 px-2 py-0.5 text-xs font-bold uppercase tracking-widest text-blue-400">
                v2
              </span>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Операционная панель
            </p>
          </div>
          <div>
            <input
              type="password"
              className="w-full rounded-md border border-border bg-zinc-800 px-4 py-2.5 text-sm placeholder:text-muted-foreground focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="Пароль"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleLogin();
              }}
              autoFocus
            />
          </div>
          {error && (
            <div className="text-center text-sm text-red-400">
              Неверный пароль
            </div>
          )}
          <button
            onClick={handleLogin}
            disabled={checking || !password}
            className="w-full rounded-md bg-blue-600 py-2.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {checking ? "Проверка…" : "Войти"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ logout }}>
      {children}
    </AuthContext.Provider>
  );
}
