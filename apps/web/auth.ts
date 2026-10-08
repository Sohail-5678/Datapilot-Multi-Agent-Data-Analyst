import NextAuth, { type NextAuthConfig, type Session, type User } from "next-auth";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import { isRole, type Role } from "@/lib/roles";

/**
 * Auth.js v5 (SPEC §10.4): "Try as guest" (no account, lower limits) and GitHub OAuth when configured.
 * Admins come from ADMIN_GITHUB_USERS. Session strategy is JWT (httpOnly, sameSite=lax, secure on https).
 */
export interface AppUser {
  sub: string;
  role: Role;
  name: string;
  login: string | null;
  image: string | null;
}

function csv(v: string | undefined) {
  return (v ?? "")
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function roleForGithubLogin(login: string): Role {
  return csv(process.env.ADMIN_GITHUB_USERS).includes(login.toLowerCase()) ? "admin" : "user";
}

// Best-effort guest sign-in throttle per instance (each guest gets its own rate-limited identity).
const attempts = new Map<string, { count: number; reset: number }>();
function allowGuest(key: string) {
  const now = Date.now();
  const e = attempts.get(key);
  if (!e || e.reset < now) {
    attempts.set(key, { count: 1, reset: now + 60 * 60_000 });
    if (attempts.size > 5000) attempts.clear();
    return true;
  }
  e.count += 1;
  return e.count <= 12;
}

const providers: NextAuthConfig["providers"] = [
  Credentials({
    id: "guest",
    name: "Guest",
    credentials: {},
    async authorize(_c, request) {
      const ip =
        request?.headers?.get("x-forwarded-for")?.split(",")[0]?.trim() || request?.headers?.get("x-real-ip") || "local";
      if (!allowGuest(ip)) return null;
      return { id: `guest:${crypto.randomUUID()}`, name: "Guest", role: "guest" } as User;
    },
  }),
];

if (process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET) {
  providers.push(GitHub({ clientId: process.env.AUTH_GITHUB_ID, clientSecret: process.env.AUTH_GITHUB_SECRET }));
}

export const authConfig = {
  trustHost: true,
  session: { strategy: "jwt", maxAge: 60 * 60 * 24 * 7 },
  pages: { signIn: "/login", error: "/login" },
  providers,
  callbacks: {
    async jwt({ token, user, account, profile }) {
      if (user && account) {
        if (account.provider === "github") {
          const gh = (profile ?? {}) as { id?: number | string; login?: string; name?: string };
          const login = String(gh.login ?? "");
          token.sub = `github:${gh.id ?? account.providerAccountId}`;
          token.role = roleForGithubLogin(login);
          token.login = login;
          token.name = gh.name || login;
        } else {
          token.sub = String(user.id);
          token.role = "guest";
          token.login = null;
          token.name = "Guest";
        }
      }
      return token;
    },
    async session({ session, token }) {
      const app: AppUser = {
        sub: String(token.sub ?? ""),
        role: isRole(token.role) ? token.role : "guest",
        name: typeof token.name === "string" ? token.name : "Guest",
        login: typeof token.login === "string" ? token.login : null,
        image: typeof token.picture === "string" ? token.picture : null,
      };
      return { ...session, app } as Session;
    },
  },
} satisfies NextAuthConfig;

export const { handlers, auth, signIn, signOut } = NextAuth(authConfig);

export function appUserFrom(session: Session | null | undefined): AppUser | null {
  const app = (session as (Session & { app?: AppUser }) | null | undefined)?.app;
  if (!app || !app.sub || !isRole(app.role)) return null;
  return app;
}

export async function getAppUser(): Promise<AppUser | null> {
  return appUserFrom(await auth());
}
