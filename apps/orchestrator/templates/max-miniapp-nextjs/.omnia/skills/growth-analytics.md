# Growth & Analytics

This is not a template. Measure whether users receive value; do not turn the
interface into an experiment or dark-pattern surface.

## Event architecture

Start with a short outcome tree: product promise → activation → repeated value →
retention. Define events from observable facts with stable names and minimal
properties. Use the managed `/api/omnia/events` path and exclude personal data.

Recommended event shape:

- `flow_started` with entry context;
- one domain-specific activation event after persisted success;
- `flow_failed` with safe error category, never raw input;
- `value_repeated` for the meaningful return loop;
- `feature_discovered` only when it changes a product decision.

Avoid tracking every click. Page views and button taps are diagnostics, not
proof of value.

## Funnels and retention

Define the denominator and time window. A funnel step must be ordered and
necessary; do not hide users who fail. Choose a retention action that represents
received value, not opening a notification. Segment only where it can change a
decision and avoid tiny, identifying cohorts.

## Experiments

Write hypothesis, primary metric, guardrail and stopping rule before changing UI.
Randomise once per user, persist assignment, keep experiences functionally
equivalent and never experiment on consent, security, payment truth or critical
accessibility. Provide a default when assignment is unavailable.

Growth mechanics must preserve user agency. No fake urgency, forced sharing,
punitive streaks or obstructive cancellation.
