/** Where the access token lives between requests.
 *
 * Held in memory and mirrored to localStorage, so a reload does not sign the
 * user out. That is a deliberate trade: a token in localStorage is readable by
 * any script that gets onto the page, and the alternative — an httpOnly cookie
 * — needs the API and the interface to share an origin, which they do not here
 * (the API answers on 8001, the interface on 5174). The token is short-lived
 * and carries no secret beyond the session it represents.
 */

const STORAGE_KEY = "tender.session";

export interface SessionUser {
  id: string;
  email: string;
  role: "admin" | "manager" | "estimator" | "viewer";
}

export interface Session {
  token: string;
  user: SessionUser;
  /** Epoch milliseconds. Used to drop a token the API would reject anyway. */
  expiresAt: number;
}

let current: Session | null = null;
const listeners = new Set<(session: Session | null) => void>();

function read(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Session;
    if (!parsed.token || !parsed.user) return null;
    // An expired token would only produce a confusing 401 on the next call.
    if (parsed.expiresAt && parsed.expiresAt < Date.now()) return null;
    return parsed;
  } catch {
    // Private browsing, blocked storage, or a half-written value: treat it as
    // signed out rather than letting the whole interface fail to start.
    return null;
  }
}

current = read();

export function getSession(): Session | null {
  if (current && current.expiresAt && current.expiresAt < Date.now()) {
    setSession(null);
  }
  return current;
}

export function getToken(): string | null {
  return getSession()?.token ?? null;
}

export function setSession(session: Session | null): void {
  current = session;
  try {
    if (session) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage is unavailable; the in-memory copy still carries this tab.
  }
  for (const listener of listeners) listener(session);
}

export function onSessionChange(listener: (session: Session | null) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
