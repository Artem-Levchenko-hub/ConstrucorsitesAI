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
    <div className="min-h-svh flex items-center justify-center px-6 py-12 bg-surface-base">
      <div className="w-full max-w-sm space-y-8">
        <Link
          href="/"
          className="flex items-center gap-2 text-fg-primary font-semibold tracking-tight"
        >
          <span className="inline-block h-5 w-5 rounded-md bg-accent" />
          <span>Omnia.AI</span>
        </Link>

        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-fg-secondary">{subtitle}</p>
        </div>

        <div className="space-y-6">{children}</div>

        <div className="text-sm text-fg-secondary">{footer}</div>
      </div>
    </div>
  );
}
