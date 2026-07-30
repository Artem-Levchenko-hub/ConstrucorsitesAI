import Link from "next/link";

export function BrandMark({
  inverse = false,
  href = "/",
}: {
  inverse?: boolean;
  href?: string;
}) {
  return (
    <Link
      href={href}
      className="font-display inline-flex items-center gap-2.5 font-semibold tracking-[-0.025em]"
      aria-label="Omnia — главная"
    >
      <span
        className={`grid h-8 w-8 grid-cols-2 gap-[3px] rounded-lg border p-[6px] shadow-[0_0_24px_rgba(59,130,246,0.12)] ${
          inverse ? "border-blue-400/40 bg-blue-500/10" : "border-[#13172a]"
        }`}
        aria-hidden
      >
        <span className={inverse ? "rounded-[2px] bg-blue-300" : "rounded-[2px] bg-[#13172a]"} />
        <span className={inverse ? "rounded-[2px] bg-blue-500" : "rounded-[2px] bg-[#3b82f6]"} />
        <span className={inverse ? "rounded-[2px] bg-violet-500" : "rounded-[2px] bg-[#3b82f6]"} />
        <span className={inverse ? "rounded-[2px] bg-white" : "rounded-[2px] bg-[#13172a]"} />
      </span>
      <span className="text-[18px]">Omnia</span>
    </Link>
  );
}
