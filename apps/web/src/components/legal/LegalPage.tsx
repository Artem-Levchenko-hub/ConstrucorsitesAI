import Link from "next/link";

export function LegalPage({
  title,
  updated = "30 июля 2026",
  children,
}: {
  title: string;
  updated?: string;
  children: React.ReactNode;
}) {
  return (
    <main className="min-h-screen bg-[#0b0c12] px-5 py-10 text-white">
      <article className="mx-auto max-w-3xl">
        <Link href="/" className="text-sm text-[#7897f4] hover:text-white">
          ← Lead Generator
        </Link>
        <h1 className="mt-10 text-4xl font-semibold tracking-[-0.04em]">{title}</h1>
        <p className="mt-3 text-sm text-white/35">Редакция от {updated}</p>
        <div className="legal-copy mt-10 space-y-8 text-[15px] leading-7 text-white/65">
          {children}
        </div>
        <div className="mt-12 border-t border-white/10 pt-6 text-sm text-white/35">
          Вопросы по документу:{" "}
          <a className="text-[#7897f4]" href="mailto:support@lead-generator.ru">
            support@lead-generator.ru
          </a>
        </div>
      </article>
    </main>
  );
}

export function LegalSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h2 className="mb-3 text-xl font-medium text-white">{title}</h2>
      {children}
    </section>
  );
}
