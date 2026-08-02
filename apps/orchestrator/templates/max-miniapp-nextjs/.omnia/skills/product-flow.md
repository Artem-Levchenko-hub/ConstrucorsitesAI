# Product Flow

This is not a template. It is a decision protocol for turning a brief into a
small, complete mobile product that earns the next user action.

## Required decisions

1. Name the user's situation before opening the app, the job they came to do,
   and the evidence that tells them it worked.
2. Choose one primary loop. Express it as: entry cue → decision → action →
   feedback → useful next move. Secondary features must support that loop.
3. Model the domain objects and their relationships before naming screens. A
   workout has exercises and sets; a booking has service, slot and participant.
4. Design information architecture from user questions, not feature buckets.
   A destination earns a place only when it answers a recurring question.
5. Put the shortest useful experience before setup. Ask only for information
   required to produce the next visible benefit; defer preferences and profile
   enrichment.

## Onboarding and activation

- Define the activation event as an observable user outcome, not opening the
  app or viewing a tour.
- Prefer progressive onboarding inside the first real task. Use an intro only
  when the product has an unfamiliar mental model, material risk or permissions.
- Preserve user effort across permission denial, validation errors, reload and
  MAX WebView interruption.
- Give every empty state a reason, one relevant action and believable sample
  content only when it cannot be mistaken for user data.

## Flow proof before implementation

Write a compact flow map with happy path, one recoverable error, one empty
state, one loading state and the return path. Check that back navigation never
loses work and that no screen is a dead end. Then implement the smallest screen
set that completes the primary loop; do not pad the app to look larger.

## Acceptance questions

- Can a first-time user name what to do within five seconds?
- Does the first meaningful result arrive before unnecessary configuration?
- Is success specific and does it reveal the next useful move?
- Can the user resume after refresh, reconnect or a dismissed sheet?
