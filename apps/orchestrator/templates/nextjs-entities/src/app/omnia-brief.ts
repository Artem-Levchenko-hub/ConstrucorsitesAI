// Per-project art-director brief payload (palette / fonts / section names /
// motion). The default ships `null` so the app compiles and the brief-narration
// reveal stays inert; on every full build Omnia overwrites this file with the
// project's real brief (services/brief_narration.inject_brief_module). Consumed
// by src/app/layout.tsx → window.__omniaBrief, which public/omnia-brief-narration.js
// turns into the "AI is designing this" reveal on the shared public surface — so
// a stranger opening the live (or forked) app sees the SAME design birth the
// owner saw in the workspace, not a silent finished UI.
export const brief: Record<string, unknown> | null = null;
