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
    <main className="studio-grid min-h-screen bg-[#080a10] px-5 py-10 text-white">
      <article className="mx-auto max-w-3xl rounded-2xl border border-[#1e243f] bg-[#0f121f] p-6 sm:p-10">
        <Link href="/" className="text-sm text-blue-400 hover:text-white">
          ← Omnia
        </Link>
        <h1 className="mt-10 text-4xl font-semibold tracking-[-0.04em] sm:text-5xl">{title}</h1>
        <p className="mt-3 text-xs uppercase tracking-[0.12em] text-slate-600">Редакция от {updated}</p>
        <div className="legal-copy mt-10 space-y-8 text-[15px] leading-7 text-slate-400">
          {children}
        </div>
        <div className="mt-12 border-t border-[#1e243f] pt-6 text-sm text-slate-500">
          Вопросы по документу:{" "}
          <a className="text-blue-400" href="mailto:support@lead-generator.ru">
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
      <h2 className="mb-3 text-xl font-semibold text-white">{title}</h2>
      {children}
    </section>
  );
}
