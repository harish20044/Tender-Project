import { useEffect, useState } from "react";

import type { Session } from "../lib/session";
import { getSession, onSessionChange } from "../lib/session";

/** The signed-in session, kept in step with the store.
 *
 * The store is the single copy — the API client reads it on every request —
 * and this subscribes rather than holding a second one, so a 401 that clears
 * the session anywhere in the app is reflected everywhere at once.
 */
export function useSession(): Session | null {
  const [session, setSessionState] = useState<Session | null>(getSession);

  useEffect(() => onSessionChange(setSessionState), []);

  return session;
}
