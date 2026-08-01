# Mobile quality and accessibility

- Use semantic controls and landmarks before ARIA. Every interactive element must
  be reachable and operable without relying on pointer hover.
- Keep primary touch targets approximately 44×44 CSS px or larger with separation
  that prevents accidental activation. The visual icon may be smaller inside.
- Maintain readable contrast in every state, including muted text, disabled
  controls, focus, destructive actions and text over imagery.
- Preserve visible keyboard focus and logical DOM/focus order. When a sheet or
  dialog opens, move focus meaningfully and restore it on close.
- Associate labels, descriptions and errors with inputs. Do not use placeholders
  as labels; keep the user's value after validation or network failure.
- Support dynamic Russian copy: allow longer labels, plural forms, large numbers
  and 200% text zoom without clipped controls or horizontal page scrolling.
- Announce meaningful async status without repeatedly interrupting assistive
  technology. Skeletons and spinners need text equivalents when the wait matters.
- Test loading, empty, error/retry, success, selected, pressed and disabled states
  at both 360 and 390px, with reduced motion and a software keyboard assumption.
- Avoid disabled-looking active controls, low-opacity body text, tiny chart labels
  and colour-only status.

Accessibility is part of the art direction: hierarchy, contrast and interaction
clarity should make the product feel more intentional, not more generic.
