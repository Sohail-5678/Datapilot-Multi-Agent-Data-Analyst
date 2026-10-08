import type { Metadata } from "next";
import { RunDetail } from "@/components/trace/run-detail";
import { SiteFooter } from "@/components/shell/site-footer";
import { SiteHeader } from "@/components/shell/site-header";

export const metadata: Metadata = { title: "Run trace" };

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return (
    <>
      <SiteHeader />
      <main id="main" className="relative z-10 mx-auto max-w-[1200px] px-4 pt-10 sm:px-8">
        <RunDetail runId={runId} />
      </main>
      <SiteFooter />
    </>
  );
}
