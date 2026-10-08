import { getAppUser } from "@/auth";
import { Logo } from "@/components/brand/logo";
import { NavLinks, UserMenu } from "@/components/shell/nav";
import { ThemeToggle } from "@/components/shell/theme";
import { cn } from "@/lib/utils";

/** Editorial masthead, after the reference: ✦ logo · spaced small-caps nav · hairline · three-line motto. */
export async function SiteHeader({ variant = "paper" }: { variant?: "paper" | "navy" }) {
  const user = await getAppUser();
  const navy = variant === "navy";
  return (
    <header className={cn("relative z-30", navy ? "text-ivory" : "text-ink")}>
      <div className="mx-auto flex h-20 max-w-[1440px] items-center gap-6 px-4 sm:px-8">
        <Logo />
        <NavLinks navy={navy} />
        <div className="ml-auto flex items-center gap-3 sm:gap-5">
          <div className="hidden items-center gap-4 xl:flex">
            <span className={cn("h-8 w-px", navy ? "bg-ivory/30" : "bg-line-strong")} />
            <p className="font-mono text-[0.55rem] leading-[1.35] tracking-[0.24em] opacity-80">
              VERIFIED
              <br />
              ANSWERS, NOT
              <br />
              GUESSES.
            </p>
          </div>
          <ThemeToggle />
          <UserMenu user={user ? { name: user.name, role: user.role, image: user.image, login: user.login } : null} navy={navy} />
        </div>
      </div>
      <div className="mx-auto max-w-[1440px] px-4 sm:px-8">
        <div className={cn("h-px", navy ? "bg-ivory/15" : "bg-line")} />
      </div>
    </header>
  );
}
