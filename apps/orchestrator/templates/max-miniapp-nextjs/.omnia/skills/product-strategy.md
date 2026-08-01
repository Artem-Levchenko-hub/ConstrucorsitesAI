# Product strategy lens

Use this pack to discover the smallest complete product, not the smallest amount
of UI.

1. Write the user's job as: situation → motivation → expected change. The main
   screen should expose that change and the next useful action immediately.
2. Separate the happy path from supporting paths. A useful mobile product usually
   needs one dominant loop, two to four supporting destinations, and a clear way
   back—not a wall of unrelated features.
3. Build information architecture from user decisions. Group content by what the
   user is trying to decide now; do not mirror database entities or generate a
   generic dashboard.
4. Turn every noun in the brief into behaviour: what can be inspected, changed,
   confirmed, undone or resumed? Decorative controls are defects.
5. Model the experience as states and transitions. Include first use, loading,
   useful empty, partial data, success, validation failure, network failure,
   permission denial and recovery where relevant.
6. Prefer progressive disclosure: show the decision summary first, details on
   demand, and advanced controls only when the task calls for them.
7. Use realistic Russian content and data whose relationships tell a coherent
   story. Demo data should prove the interaction, not fill rectangles.

Before code, ensure the selected direction in `max-design-spec.json` explains how
the composition makes the primary action faster or clearer. Visual novelty with
no product advantage is not a direction.
