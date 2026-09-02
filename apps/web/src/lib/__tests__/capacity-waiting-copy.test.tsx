import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentTranscript } from "@/components/workspace/AgentTranscript";

describe("AgentTranscript capacity fallback", () => {
  it("renders durable waiting copy even when persisted steps are absent", () => {
    const queryClient = new QueryClient();
    const html = renderToStaticMarkup(
      <QueryClientProvider client={queryClient}>
        <AgentTranscript
          projectId="00000000-0000-0000-0000-000000000001"
          messageId="00000000-0000-0000-0000-000000000002"
          streaming
          initialSteps={[]}
          generationStatus="queued_for_capacity"
        />
      </QueryClientProvider>,
    );

    expect(html).toContain("Ожидаю ресурсы сервера");
    expect(html).toContain("запустится автоматически");
  });
});
