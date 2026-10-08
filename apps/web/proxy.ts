import { NextResponse } from "next/server";
import { auth, appUserFrom } from "@/auth";
import { isProtectedPage } from "@/lib/roles";

/** Route guard (Next 16 `proxy`). UI convenience only — the backend re-checks every request. */
export const proxy = auth((req) => {
  const { pathname, search } = req.nextUrl;
  if (isProtectedPage(pathname) && !appUserFrom(req.auth)) {
    const url = new URL("/login", req.nextUrl.origin);
    url.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|sandbox|favicon.ico|icon.svg|opengraph-image|robots.txt|.*\\.(?:png|jpg|jpeg|svg|webp|ico|txt|js|json)$).*)"],
};
