import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SiteHeader } from "@/components/shell/site-header";
import { Workspace } from "@/components/workspace/workspace";

export const metadata: Metadata = { title: "Conversation" };

export default async function ThreadPage({ params }: { params: Promise<{ threadId: string }> }) {
  const { threadId } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(threadId)) notFound();
  return (
    <>
      <SiteHeader />
      <Workspace key={threadId} initialDb="chinook" threadId={threadId} />
    </>
  );
}
