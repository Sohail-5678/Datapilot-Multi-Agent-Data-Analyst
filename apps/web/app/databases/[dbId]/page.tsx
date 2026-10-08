import type { Metadata } from "next";
import { DatabaseDetail } from "@/components/db/detail";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";

export const metadata: Metadata = { title: "Database" };

export default async function DatabasePage({ params }: { params: Promise<{ dbId: string }> }) {
  const { dbId } = await params;
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-[1440px] px-4 pt-10 sm:px-8">
        <DatabaseDetail dbId={dbId} />
      </main>
      <SiteFooter />
    </>
  );
}
