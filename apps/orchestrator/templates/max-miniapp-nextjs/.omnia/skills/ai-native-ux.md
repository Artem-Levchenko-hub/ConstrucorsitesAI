# AI-native product experience

AI should change what the user can accomplish, not merely add a chat-shaped card.

1. Define the AI job, the context it needs and the concrete output the user can
   act on. Use the managed `requestOmniaAI({ message, instructions, context })`
   client; never fake intelligence with timers, random values or static text.
2. Collect context progressively. Show what data will be used and let the user
   correct high-impact inputs before an expensive request.
3. Design the request lifecycle: editable prompt/input, submit/disabled state,
   visible processing, successful result, provider/network failure, retry and a
   safe way back to the user's input.
4. Make output scannable and actionable. Lead with the conclusion, attach evidence
   or source data available inside the product, and offer a next action such as
   apply, save, compare, refine or discard.
5. Calibrate trust. Distinguish facts, user-provided context and model inference;
   express uncertainty where consequences matter. Never fabricate completion.
6. Preserve user agency: generated changes should be previewable, reversible or
   explicitly confirmed when they affect stored data.
7. Handle harmful, unsupported or out-of-domain requests in product voice without
   destroying the rest of the workflow.

Avoid a generic assistant persona unless conversation itself is the best
interaction. AI can be an analyser, coach, transformer, recommender or copilot
embedded directly in the primary flow.
