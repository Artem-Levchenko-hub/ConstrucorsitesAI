import Link from "next/link";

import { YleumMark } from "@/components/brand/YleumMark";

export function BrandMark({
  inverse = false,
  href = "/",
  label = "Yleum",
}: {
  inverse?: boolean;
  href?: string;
  label?: string;
}) {
  return (
    <Link
      href={href}
      className="font-display inline-flex items-center gap-2 font-semibold tracking-[-0.025em]"
      aria-label={`${label} — главная`}
    >
      <span
        className={`flex h-8 w-8 items-center justify-center rounded-[8px] ${
          inverse ? "bg-white/10" : "bg-white ring-1 ring-inset ring-black/5"
        }`}
        aria-hidden
      >
        <YleumMark className="h-[22px] w-[22px]" />
      </span>
      <span className="text-[18px] text-fg-primary">
        {label}
      </span>
    </Link>
  );
}
