import type { Metadata } from "next";
import { DatabaseCatalog } from "@/components/db/catalog";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";

export const metadata: Metadata = { title: "Databases" };

export default function DatabasesPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-[1440px] px-4 pt-12 sm:px-8">
        <p className="eyebrow text-accent">The archive</p>
        <h1 className="display mt-3 text-5xl sm:text-6xl">Five demo databases.</h1>
        <p className="mt-5 max-w-2xl text-[0.98rem] leading-relaxed text-muted">
          A music store, comic characters, a student club, seventy years of Formula 1 and a European football archive. All are baked
          into the server as read-only SQLite files; personal-data columns are masked here and never sent to a model as samples.
        </p>
        <DatabaseCatalog />
      </main>
      <SiteFooter />
    </>
  );
}
