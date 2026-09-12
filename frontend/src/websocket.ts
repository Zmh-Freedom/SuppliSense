import type { WSEventMap } from './types';

type EventHandler<T = unknown> = (data: T) => void;

export class WSClient {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<EventHandler<unknown>>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private reconnectEnabled = true;

  connect() {
    this.reconnectEnabled = true;
    if (this.ws?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;

    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
    };

    this.ws.onmessage = (msg) => {
      try {
        const { event, data } = JSON.parse(msg.data);
        const handlers = this.handlers.get(event);
        if (handlers) {
          handlers.forEach(fn => fn(data));
        }
      } catch {
        // Ignore parse errors
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      if (this.reconnectEnabled) this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      this.connect();
    }, this.reconnectDelay);
  }

  on<K extends keyof WSEventMap>(event: K, handler: EventHandler<WSEventMap[K]>): () => void;
  on(event: string, handler: EventHandler<unknown>): () => void;
  on(event: string, handler: EventHandler<unknown>): () => void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
    return () => this.off(event, handler);
  }

  off(event: string, handler: EventHandler<unknown>) {
    this.handlers.get(event)?.delete(handler);
  }

  disconnect() {
    // Closing a socket fires onclose asynchronously. Disable reconnects before
    // closing so logout/unmount cannot schedule an anonymous retry.
    this.reconnectEnabled = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  destroy() {
    this.disconnect();
    this.handlers.clear();
  }
}

export const wsClient = new WSClient();
