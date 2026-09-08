import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openRealStream } from "@/lib/prompt-stream-transport";

class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: Socket[] = [];
  readyState = Socket.OPEN;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send = vi.fn<(data: string) => void>();
  close = vi.fn(() => { this.readyState = 3; this.onclose?.(); });
  constructor(readonly url: string) { Socket.instances.push(this); }
}
const lastSocket = () => Socket.instances.at(-1)!;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", Socket);
  vi.stubEnv("NEXT_PUBLIC_WS_URL", "wss://fixture.invalid");
  Socket.instances = [];
});
afterEach(() => {
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs();
});

describe("prompt stream transport", () => {
  it.each<readonly [Parameters<typeof openRealStream>[2], string]>([
    [{ runId: "run /&", afterSeq: 17 }, "?run_id=run%20%2F%26&after_seq=17"],
    [{ runId: "run", afterSeq: -2 }, "?run_id=run&after_seq=0"],
    [{ runId: "run" }, "?run_id=run&after_seq=0"],
    [{ afterSeq: 17 }, ""],
  ])("preserves replay query %j", (options, query) => {
    openRealStream("p", vi.fn(), options);
    expect(lastSocket().url).toBe(`wss://fixture.invalid/api/ws/projects/p${query}`);
  });
  it("uses browser host when the configured WS base is absent", () => {
    vi.stubEnv("NEXT_PUBLIC_WS_URL", undefined);
    openRealStream("p", vi.fn());
    expect(lastSocket().url).toBe(`wss://${window.location.host}/api/ws/projects/p`);
  });
  it("pings exactly every 25 seconds only while OPEN", () => {
    openRealStream("p", vi.fn()); const ws = lastSocket();
    vi.advanceTimersByTime(24_999); expect(ws.send).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1); expect(ws.send).toHaveBeenCalledExactlyOnceWith('{"type":"ping"}');
    ws.readyState = 0; vi.advanceTimersByTime(25_000); expect(ws.send).toHaveBeenCalledTimes(1);
    ws.readyState = 1; vi.advanceTimersByTime(25_000); expect(ws.send).toHaveBeenCalledTimes(2);
  });
  it("sends JSON control frames only while OPEN", () => {
    const ctl = openRealStream("p", vi.fn()); const ws = lastSocket();
    ctl.send({ type: "resync" }); expect(ws.send).toHaveBeenCalledExactlyOnceWith('{"type":"resync"}');
    for (const state of [0, 2, 3]) { ws.readyState = state; ctl.send({ type: "resync" }); }
    expect(ws.send).toHaveBeenCalledTimes(1);
  });
  it.each([0, 1, 2, 3])("cancel at readyState=%i clears heartbeat and suppresses close callback", (state) => {
    const onClose = vi.fn(); const ctl = openRealStream("p", vi.fn(), { onClose });
    const ws = lastSocket(); ws.readyState = state; ctl.cancel();
    expect(ws.close).toHaveBeenCalledTimes(state <= 1 ? 1 : 0);
    expect(ws.onclose).toBeNull(); expect(onClose).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
  it("unexpected close clears heartbeat before invoking the callback", () => {
    const onClose = vi.fn(() => expect(vi.getTimerCount()).toBe(0));
    const onOpen = vi.fn(); openRealStream("p", vi.fn(), { onClose, onOpen });
    lastSocket().onopen?.(); expect(onOpen).toHaveBeenCalledTimes(1);
    lastSocket().onclose?.(); expect(onClose).toHaveBeenCalledTimes(1);
  });
  it("ignores malformed JSON and also swallows apply exceptions", () => {
    const apply = vi.fn(() => { throw new Error("apply failed"); });
    openRealStream("p", apply); const ws = lastSocket();
    expect(() => ws.onmessage?.({ data: "{" })).not.toThrow(); expect(apply).not.toHaveBeenCalled();
    expect(() => ws.onmessage?.({ data: '{"type":"fixture"}' })).not.toThrow();
    expect(apply).toHaveBeenCalledExactlyOnceWith({ type: "fixture" });
  });
});
