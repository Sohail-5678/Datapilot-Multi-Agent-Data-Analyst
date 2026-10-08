"use server";

import { redirect } from "next/navigation";
import { signIn, signOut } from "@/auth";

function safeNext(next: FormDataEntryValue | null) {
  const n = typeof next === "string" ? next : "/app";
  return n.startsWith("/") && !n.startsWith("//") ? n : "/app";
}

export async function guestSignInAction(formData: FormData) {
  const next = safeNext(formData.get("next"));
  try {
    await signIn("guest", { redirectTo: next });
  } catch (e) {
    // Auth.js signals success with a redirect error; rethrow it, map real failures to the login page.
    if (e && typeof e === "object" && "digest" in e && String((e as { digest?: string }).digest).startsWith("NEXT_REDIRECT")) throw e;
    redirect(`/login?error=guest_limit&next=${encodeURIComponent(next)}`);
  }
}

export async function githubSignInAction(formData: FormData) {
  await signIn("github", { redirectTo: safeNext(formData.get("next")) });
}

export async function signOutAction() {
  await signOut({ redirectTo: "/" });
}
