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
    client.disconnect();

    vi.advanceTimersByTime(30_000);

    expect(sockets).toHaveLength(1);
  });

  it('keeps reconnecting after an unexpected close', () => {
    const client = new WSClient();
    client.connect();
    sockets[0]?.triggerClose();

    vi.advanceTimersByTime(1_000);

    expect(sockets).toHaveLength(2);
    client.disconnect();
  });
});
