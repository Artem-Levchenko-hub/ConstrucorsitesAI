import Link from "next/link";

export function AuthCard({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div className="min-h-svh flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm space-y-8">
        <Link
          href="/"
          className="flex items-center justify-center gap-2 text-fg-primary font-semibold tracking-tight"
        >
          <span className="inline-block h-7 w-7 rounded-lg bg-[linear-gradient(135deg,#7c5cff_0%,#a48aff_100%)] shadow-[0_8px_24px_-6px_rgba(124,92,255,0.6)]" />
          <span>Omnia.AI</span>
        </Link>

        <div className="rounded-[22px] border border-border-subtle bg-surface-raised/80 backdrop-blur-xl p-8 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.6),0_0_0_1px_rgba(124,92,255,0.10),0_30px_80px_-30px_rgba(124,92,255,0.25)] space-y-6">
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-fg-secondary">{subtitle}</p>
          </div>

          <div className="space-y-6">{children}</div>
        </div>

        <div className="text-sm text-fg-secondary text-center">{footer}</div>
      </div>
    </div>
  );
}
