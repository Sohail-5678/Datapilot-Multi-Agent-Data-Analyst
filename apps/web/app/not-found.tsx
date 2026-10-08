import Link from "next/link";
import { SiteHeader } from "@/components/shell/site-header";

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-2xl px-4 py-28 text-center">
        <p className="font-serif text-8xl italic text-accent">404</p>
        <h1 className="display mt-4 text-4xl">Off the chart.</h1>
        <p className="mt-3 text-muted">This page doesn&apos;t exist — or it belongs to someone else.</p>
        <Link href="/" className="btn btn-primary mt-8">Back home</Link>
      </main>
    </>
  );
}
