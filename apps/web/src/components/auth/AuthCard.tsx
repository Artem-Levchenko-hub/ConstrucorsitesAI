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
          <span className="inline-block h-7 w-7 rounded-sm bg-accent" />
          <span>Omnia.AI</span>
        </Link>

        <div className="rounded-[22px] border border-border-subtle bg-surface-raised/80 backdrop-blur-xl p-8 space-y-6">
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
