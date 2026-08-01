"use client";

import { useRouter } from "next/navigation";
import { useLocale } from "next-intl";
import { LOCALES } from "@/i18n/config";

function persistLocale(locale: string) {
  document.cookie = `NEXT_LOCALE=${locale}; path=/; max-age=31536000; samesite=lax`;
}

export function LocaleSwitcher({ inverse = false }: { inverse?: boolean }) {
  const locale = useLocale();
  const router = useRouter();

  const set = (l: string) => {
    persistLocale(l);
    router.refresh();
  };

  return (
    <div
      className={[
        "flex h-7 items-center gap-0.5 rounded-full border px-1 text-[12px] font-mono",
        inverse ? "border-white/35 bg-white/[0.04]" : "border-separator",
      ].join(" ")}
    >
      {(LOCALES as readonly string[]).map((l, i) => (
        <button
          key={l}
          onClick={() => set(l)}
          className={[
            "px-2 h-5 rounded-full transition-colors uppercase tracking-wider",
            l === locale
              ? "bg-accent text-accent-fg"
              : inverse
                ? "text-slate-300 hover:bg-white/[0.08] hover:text-white"
                : "text-label-2 hover:text-label-1",
            i > 0 ? "ml-0.5" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          aria-pressed={l === locale}
          aria-label={`Switch language to ${l.toUpperCase()}`}
        >
          {l}
        </button>
      ))}
    </div>
  );
}
