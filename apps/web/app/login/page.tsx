import { ArrowRight } from "lucide-react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getAppUser } from "@/auth";
import { githubSignInAction, guestSignInAction } from "@/app/actions";
import { Astrolabe } from "@/components/brand/astrolabe";
import { Logo } from "@/components/brand/logo";
import { serverEnv } from "@/lib/server/env";

export const metadata: Metadata = { title: "Sign in" };

function GithubMark() {
  return (
    <svg viewBox="0 0 16 16" className="size-4" aria-hidden fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function safeNext(n: string | string[] | undefined) {
  const v = typeof n === "string" ? n : "/app";
  return v.startsWith("/") && !v.startsWith("//") ? v : "/app";
}

export default async function Login({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const next = safeNext(sp.next);
  if (await getAppUser()) redirect(next);
  const github = serverEnv.githubEnabled();
  const error = sp.error;
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_1.1fr]">
      <main id="main" className="relative z-10 flex flex-col justify-between px-6 py-8 sm:px-12">
        <Logo />
        <div className="mx-auto w-full max-w-md py-16">
          <p className="eyebrow text-accent">Welcome aboard</p>
          <h1 className="display mt-4 text-5xl">Sign in to ask your first question.</h1>
          <p className="mt-5 text-[0.95rem] leading-relaxed text-muted">
            Guests can ask 10 questions an hour on five demo databases. GitHub sign-in raises that to 30 and keeps your conversations.
          </p>
          {error === "guest_limit" && (
            <p className="mt-6 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">Too many guest sessions from this network. Try again later or sign in with GitHub.</p>
          )}
          {typeof error === "string" && error !== "guest_limit" && (
            <p className="mt-6 rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">Sign-in didn&apos;t complete. Please try again.</p>
          )}
          <div className="mt-10 space-y-3">
            <form action={guestSignInAction}>
              <input type="hidden" name="next" value={next} />
              <button type="submit" className="btn btn-crimson w-full !py-3.5">
                Try as guest <ArrowRight className="size-4" />
              </button>
            </form>
            {github ? (
              <form action={githubSignInAction}>
                <input type="hidden" name="next" value={next} />
                <button type="submit" className="btn btn-primary w-full !py-3.5">
                  <GithubMark /> Continue with GitHub
                </button>
              </form>
            ) : (
              <p className="text-center text-xs text-muted">GitHub sign-in isn&apos;t configured on this deployment.</p>
            )}
          </div>
        </div>
        <p className="font-mono text-[0.6rem] tracking-[0.22em] text-muted">DEMO DATA ONLY · READ-ONLY · $0 TO RUN</p>
      </main>
      <div className="relative hidden overflow-hidden bg-navy lg:block">
        <Astrolabe className="absolute -right-24 top-1/2 w-[130%] max-w-none -translate-y-1/2 opacity-90" />
        <p className="absolute bottom-10 left-10 max-w-sm font-serif text-3xl italic leading-snug text-ivory">
          “Every number in the answer is checked against the data.”
        </p>
      </div>
    </div>
  );
}
