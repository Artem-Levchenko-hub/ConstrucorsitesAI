import { afterEach, expect, it, vi } from "vitest";
import { connectSupportEvents } from "@/hooks/useSupportEvents";
class Socket {
  static OPEN = 1; static instances: Socket[] = [];
  readyState = 1; onopen: (() => void) | null = null; onmessage: ((event: { data: string }) => void) | null = null; onclose: ((event: { code: number }) => void) | null = null;
  send = vi.fn(); close = vi.fn(); constructor(public url: string) { Socket.instances.push(this); }
  event(payload: unknown) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); Socket.instances = []; });
it("keeps a guest capability out of URLs, refetches on ready, deduplicates events and reconnects", () => {
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket); const changed = vi.fn(), ready = vi.fn();
  const close = connectSupportEvents({ ticket_id: "ticket1", guest_token: "secret" }, { changed, ready });
  const ws = Socket.instances[0]; expect(ws.url).not.toContain("secret"); ws.onopen?.();
  expect(JSON.parse(ws.send.mock.calls[0][0])).toEqual({ type: "subscribe", ticket_id: "ticket1", guest_token: "secret" });
  ws.event({ type: "support.ready" }); expect(ready).toHaveBeenCalledTimes(1);
  const event = { type: "support.changed", ticket_id: "ticket1", version: 2, reason: "message" };
  ws.event(event); ws.event(event); ws.event({ ...event, version: 1 }); expect(changed).toHaveBeenCalledTimes(1);
  vi.advanceTimersByTime(25_000); expect(ws.send).toHaveBeenLastCalledWith('{"type":"ping"}');
  ws.onclose?.({ code: 1006 }); vi.advanceTimersByTime(3_000); expect(Socket.instances).toHaveLength(2);
  Socket.instances[1].event({ type: "support.ready" }); expect(ready).toHaveBeenCalledTimes(2);
  close(); vi.advanceTimersByTime(60_000); expect(Socket.instances).toHaveLength(2);
});
it("reconnects a policy-closed socket only after revalidation confirms access", async () => {
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket);
  let confirm!: (allowed: boolean) => void;
  const revalidate = vi.fn(() => new Promise<boolean>(resolve => { confirm = resolve; }));
  const close = connectSupportEvents({}, { ready: vi.fn(), changed: vi.fn(), revalidate });
  Socket.instances[0].onclose?.({ code: 1008 });
  expect(revalidate).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(30_000); expect(Socket.instances).toHaveLength(1);
  confirm(true); await vi.advanceTimersByTimeAsync(3_000); expect(Socket.instances).toHaveLength(2);
  close();
});
it("keeps denied or failed revalidation off the socket and recovers when a later check succeeds", async () => {
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket);
  const revalidate = vi.fn().mockRejectedValueOnce(new Error("temporarily unavailable")).mockResolvedValueOnce(false).mockResolvedValue(true);
  const close = connectSupportEvents({}, { ready: vi.fn(), changed: vi.fn(), revalidate });
  Socket.instances[0].onclose?.({ code: 1008 });
  await vi.advanceTimersByTimeAsync(500); expect(Socket.instances).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1_000); expect(Socket.instances).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(10_000); expect(Socket.instances).toHaveLength(2);
  close();
});
it("does not resurrect an unmounted socket when its access check completes", async () => {
  vi.useFakeTimers(); vi.stubGlobal("WebSocket", Socket);
  let confirm!: (allowed: boolean) => void;
  const close = connectSupportEvents({}, { ready: vi.fn(), changed: vi.fn(), revalidate: () => new Promise<boolean>(resolve => { confirm = resolve; }) });
  Socket.instances[0].onclose?.({ code: 1008 }); close();
  confirm(true); await vi.advanceTimersByTimeAsync(60_000); expect(Socket.instances).toHaveLength(1);
});
