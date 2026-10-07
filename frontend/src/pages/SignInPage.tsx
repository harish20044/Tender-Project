import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { signIn } from "../lib/api";

/** Sign-in.
 *
 * Credentials go to this project's API, which exchanges them with Supabase
 * server-side, so no Supabase key is shipped in this bundle. The error shown
 * is deliberately the one the API returns — it does not distinguish an
 * unknown address from a wrong password, which would tell someone probing
 * the form which half they had right.
 */
export function SignInPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = useMutation({
    mutationFn: () => signIn(email.trim(), password),
  });

  const ready = email.trim().length > 2 && password.length > 0;

  return (
    <div className="flex min-h-screen items-center justify-center bg-ground px-6">
      <div className="w-full max-w-sm">
        <header className="mb-8 flex flex-col gap-1">
          <p className="text-xs uppercase tracking-wider text-ink-faint">Bid support</p>
          <h1 className="text-2xl font-semibold text-ink">Tender Intelligence</h1>
          <p className="text-sm text-ink-muted">Sign in to review tenders.</p>
        </header>

        <form
          className="flex flex-col gap-4 rounded border border-rule bg-surface p-6"
          onSubmit={(event) => {
            event.preventDefault();
            if (ready && !submit.isPending) submit.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm" htmlFor="signin-email">
            <span className="text-ink-muted">Email</span>
            <input
              id="signin-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded border border-rule bg-canvas px-3 py-2 text-sm text-ink"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm" htmlFor="signin-password">
            <span className="text-ink-muted">Password</span>
            <input
              id="signin-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded border border-rule bg-canvas px-3 py-2 text-sm text-ink"
            />
          </label>

          {submit.isError && (
            <p className="rounded border border-sev-critical/40 bg-sev-critical/5 px-3 py-2 text-sm text-sev-critical">
              {(submit.error as Error).message}
            </p>
          )}

          <button
            type="submit"
            disabled={!ready || submit.isPending}
            className="rounded bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submit.isPending ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-4 text-xs text-ink-faint">
          Accounts are managed in Supabase. Your role — viewer, estimator, manager or
          admin — decides what you can do here.
        </p>
      </div>
    </div>
  );
}
