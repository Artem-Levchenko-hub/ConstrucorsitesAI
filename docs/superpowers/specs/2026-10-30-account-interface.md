# Account interface refinement

The user requests a clean Apple-inspired Yleum account interface: aligned support controls and readable ticket cards, compact billing spacing and heights without removing information, and pleasant page/tab transitions.

Use the existing light palette and blue accent. Controls share a 44px target; cards use natural heights, 16px corners and compact responsive padding. Long ticket subjects, email addresses, prices, allowances and deadlines must remain readable. Narrow layouts wrap rather than clip content.

Motion belongs inside AccountShell and the existing admin tab panel only. Use a short decelerating entrance with a small vertical offset. Keep the same DOM/React children during trigger changes, honor reduced-motion preferences, and remove outgoing private content immediately. Do not animate global providers, builder iframes or hold previous pages for an exit effect.

Preserve all purchase, quota, permission, query, URL, keyboard, archive, pagination and draft behavior. No dependency, pricing, API or business-data changes. Verify existing behavioral suites plus meaningful motion/state regressions; do not add CSS snapshot tests. Complete the repository delivery loop. Browser visual QA needs the authenticated browser tool; report its absence explicitly.
