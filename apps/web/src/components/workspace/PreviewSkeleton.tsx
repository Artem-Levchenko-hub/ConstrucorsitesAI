"use client";

/**
 * Realistic landing-shape skeleton for the preview iframe area.
 *
 * Instead of flat <Skeleton> rectangles that look like "loading anything",
 * we render the silhouette of an actual landing (hero block + 3 feature
 * cards + footer) so the user expects the right kind of content. A
 * diagonal shimmer wave animates across, and a small pulse-ring badge
 * "AI рендерит лендинг" sits in the top-right.
 *
 * Self-contained — no API calls, just visual. Caller decides when to
 * mount it (during AI streaming, before the first iframe load, etc.).
 */

export function PreviewSkeleton({
  label = "AI рендерит лендинг…",
}: {
  label?: string;
}) {
  return (
    <div className="relative h-full w-full overflow-hidden bg-white">
      {/* Shimmer wave overlay — diagonal sweep so the eye reads "in
          progress, not stuck". `pointer-events-none` so click-through
          works if anyone wires the skeleton over a real iframe. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-10"
        style={{
          background:
            "linear-gradient(110deg, rgba(255,255,255,0) 0%, rgba(124,92,255,0.08) 40%, rgba(92,184,255,0.12) 50%, rgba(124,92,255,0.08) 60%, rgba(255,255,255,0) 100%)",
          backgroundSize: "200% 100%",
          animation: "shimmer-sweep 2s linear infinite",
        }}
      />

      {/* Status badge — top-right, accent pill with pulse-ring dot */}
      <div className="absolute top-3 right-3 z-20 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-accent-subtle border border-accent/40 text-[11px] font-mono text-accent shadow-glow-accent">
        <span className="relative inline-flex h-1.5 w-1.5">
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-accent"
            style={{ animation: "pulse-ring 2s ease-out infinite" }}
          />
          <span className="relative h-1.5 w-1.5 rounded-full bg-accent" />
        </span>
        {label}
      </div>

      {/* Landing silhouette */}
      <div className="relative z-0 h-full p-8 flex flex-col gap-8">
        {/* Hero — kicker, 2 lines of title, subtitle, 2 buttons */}
        <div className="flex flex-col items-center gap-4 pt-12">
          <div className="h-3 w-32 rounded bg-zinc-200" />
          <div className="h-12 w-3/4 max-w-2xl rounded-lg bg-zinc-200" />
          <div className="h-12 w-2/3 max-w-xl rounded-lg bg-zinc-200" />
          <div className="h-4 w-1/2 rounded bg-zinc-100 mt-2" />
          <div className="flex gap-3 mt-3">
            <div className="h-12 w-28 rounded-xl bg-accent/30" />
            <div className="h-12 w-28 rounded-xl border-2 border-accent/30" />
          </div>
        </div>

        {/* 3 feature cards */}
        <div className="grid grid-cols-3 gap-4 mt-4 max-w-4xl mx-auto w-full">
          <div className="h-32 rounded-2xl bg-zinc-100" />
          <div className="h-32 rounded-2xl bg-zinc-100" />
          <div className="h-32 rounded-2xl bg-zinc-100" />
        </div>

        {/* Footer hint */}
        <div className="flex justify-between gap-4 mt-auto pt-8 border-t border-zinc-100">
          <div className="h-3 w-32 rounded bg-zinc-200" />
          <div className="h-3 w-48 rounded bg-zinc-200" />
        </div>
      </div>

      <style jsx>{`
        @keyframes shimmer-sweep {
          0% {
            background-position: -200% 0;
          }
          100% {
            background-position: 200% 0;
          }
        }
        @keyframes pulse-ring {
          0% {
            transform: scale(0.5);
            opacity: 1;
          }
          100% {
            transform: scale(2.4);
            opacity: 0;
          }
        }
      `}</style>
    </div>
  );
}
