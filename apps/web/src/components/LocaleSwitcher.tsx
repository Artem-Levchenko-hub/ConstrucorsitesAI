"use client";

import { useRouter } from "next/navigation";
import { useLocale } from "next-intl";
import { LOCALES } from "@/i18n/config";

export function LocaleSwitcher() {
  const locale = useLocale();
  const router = useRouter();

  const set = (l: string) => {
    document.cookie = `NEXT_LOCALE=${l}; path=/; max-age=31536000; samesite=lax`;
    router.refresh();
  };

  return (
    <div className="flex items-center gap-0.5 rounded-full border border-separator px-1 h-7 text-[12px] font-mono">
      {(LOCALES as readonly string[]).map((l, i) => (
        <button
          key={l}
          onClick={() => set(l)}
          className={[
            "px-2 h-5 rounded-full transition-colors uppercase tracking-wider",
            l === locale
              ? "bg-accent text-accent-fg"
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
