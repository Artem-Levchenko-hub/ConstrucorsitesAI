# Skills index — nextjs-postgres-drizzle

> Progressive-disclosure index (knowledge-layer plan §2). Only THIS index is
> meant to sit in the agent's system prompt (name + one-line description, ~1 line
> each). Read the full `<name>.md` with `read_file` ONLY when the task matches its
> description — that keeps rich per-concern knowledge available at near-zero
> context cost.
>
> These skills RAISE THE FLOOR of the first draft. They do NOT replace the
> deterministic gates (sast_gate, perf_a11y_gate, accept_gauntlet) — gate
> coverage is the guaranteed CEILING; a skill's auto-pull is probabilistic, so
> anything security/a11y/perf-critical is ALSO enforced by a gate.

| Skill | When to read it (description) |
|---|---|
| `security.md` | Before writing ANY server action / route handler / form / auth-touching or data-touching code. How to stay clear of the injection + secret + authz mistakes the SAST gate blocks. |
| `a11y.md` | Before writing ANY page/component with images, interactive elements, forms, or color. How to pass the axe/WCAG floor the perf-a11y gate enforces. |
| `perf.md` | Before writing pages with images, fonts, data lists, or above-the-fold content. How to hit the LCP/CLS/perf-score budget the perf-a11y gate enforces. |

Convention: each skill is short, imperative, example-led, and names the GATE that
will catch a violation — so the model knows the rule is non-negotiable.
