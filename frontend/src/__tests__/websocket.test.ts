import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WSClient } from '../websocket';

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly CONNECTING = 0;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    sockets.push(this);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  triggerClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

let sockets: FakeWebSocket[] = [];

describe('WSClient reconnect lifecycle', () => {
  beforeEach(() => {
    sockets = [];
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('does not reconnect after an intentional disconnect', () => {
    const client = new WSClient();
    client.connect();
    vi.advanceTimersByTime(0);
    client.disconnect();

    vi.advanceTimersByTime(30_000);

    expect(sockets).toHaveLength(1);
  });

  it('keeps reconnecting after an unexpected close', () => {
    const client = new WSClient();
    client.connect();
    vi.advanceTimersByTime(0);
    sockets[0]?.triggerClose();

    vi.advanceTimersByTime(1_000);
    vi.advanceTimersByTime(0);

    expect(sockets).toHaveLength(2);
    client.disconnect();
  });

  it('reports initial connection and reconnect, and unsubscribes cleanly', () => {
    const client = new WSClient();
    const connected = vi.fn();
    const off = client.onConnection(connected);
    client.connect();
    vi.advanceTimersByTime(0);
    sockets[0]?.onopen?.();
    sockets[0]?.triggerClose();
    vi.advanceTimersByTime(1_000);
    sockets[1]?.onopen?.();
    expect(connected.mock.calls).toEqual([[{ reconnected: false }], [{ reconnected: true }]]);
    off();
    client.disconnect();
    client.connect();
    vi.advanceTimersByTime(0);
    sockets[2]?.onopen?.();
    expect(connected).toHaveBeenCalledTimes(2);
    client.disconnect();
  });

  it('ignores late messages from a closed socket', () => {
    const client = new WSClient();
    const handler = vi.fn();
    client.on('company_updated', handler);
    client.connect();
    vi.advanceTimersByTime(0);
    const staleMessage = sockets[0]?.onmessage;
    client.disconnect();
    staleMessage?.({ data: JSON.stringify({ event: 'company_updated', data: {} }) });
    expect(handler).not.toHaveBeenCalled();
  });
});
