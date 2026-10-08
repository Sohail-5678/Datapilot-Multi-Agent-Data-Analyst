"use client";

import Link from "next/link";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main id="main" className="relative z-10 mx-auto max-w-2xl px-4 py-28 text-center">
      <p className="font-serif text-7xl italic text-accent">✦</p>
      <h1 className="display mt-4 text-4xl">Something went sideways.</h1>
      <p className="mt-3 text-muted">The page hit an unexpected error. Your conversations are safe.</p>
      <div className="mt-8 flex justify-center gap-3">
        <button type="button" onClick={reset} className="btn btn-primary">Try again</button>
        <Link href="/" className="btn btn-ghost">Home</Link>
      </div>
    </main>
  );
}
