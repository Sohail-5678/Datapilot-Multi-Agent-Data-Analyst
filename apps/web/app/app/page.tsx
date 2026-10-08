import type { Metadata } from "next";
import { SiteHeader } from "@/components/shell/site-header";
import { Workspace } from "@/components/workspace/workspace";

export const metadata: Metadata = { title: "Workspace" };

const DBS = ["chinook", "superhero", "student_club", "formula_1", "european_football_2"];

export default async function WorkspacePage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const sp = await searchParams;
  const db = typeof sp.db === "string" && DBS.includes(sp.db) ? sp.db : "chinook";
  const q = typeof sp.q === "string" ? sp.q.slice(0, 1000) : null;
  return (
    <>
      <SiteHeader />
      <Workspace key={db} initialDb={db} initialQuestion={q} />
    </>
  );
}
