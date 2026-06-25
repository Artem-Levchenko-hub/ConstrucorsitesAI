import { listUserChannels } from "@/lib/channels";
import { requireUser } from "@/lib/session";
import { ChatClient } from "@/components/chat/chat-client";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const user = await requireUser({ next: "/chat" });
  const channels = await listUserChannels(user.id);
  return (
    <ChatClient
      currentUserId={user.id}
      initialChannels={channels.map((c) => ({ id: c.id, title: c.title }))}
    />
  );
}
