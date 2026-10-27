import type { WSConnectionState, WSEventMap } from './types';

type EventHandler<T = unknown> = (data: T) => void;

export class WSClient {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<EventHandler<unknown>>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private reconnectEnabled = true;
  private connectedOnce = false;
  private connectionHandlers = new Set<EventHandler<WSConnectionState>>();

  connect() {
    this.reconnectEnabled = true;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    if (this.connectTimer) return;

    // React StrictMode intentionally mounts, cleans up, and mounts effects
    // again in development. Defer the actual handshake so that the first
    // cleanup can cancel before a CONNECTING socket is created.
    this.connectTimer = setTimeout(() => {
      this.connectTimer = null;
      if (!this.reconnectEnabled || (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING))) return;
      this.open();
    }, 0);
  }

  private open() {

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;

    const socket = new WebSocket(url);
    this.ws = socket;

    socket.onopen = () => {
      if (this.ws !== socket) return;
      this.reconnectDelay = 1000;
      const state = { reconnected: this.connectedOnce };
      this.connectedOnce = true;
      this.connectionHandlers.forEach(handler => handler(state));
    };

    socket.onmessage = (msg) => {
      if (this.ws !== socket) return;
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

    socket.onclose = () => {
      if (this.ws !== socket) return;
      this.ws = null;
      if (this.reconnectEnabled) this.scheduleReconnect();
    };

    socket.onerror = () => {
      if (this.ws === socket) socket.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      if (this.reconnectEnabled && !this.ws) this.open();
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

  onConnection(handler: EventHandler<WSConnectionState>): () => void {
    this.connectionHandlers.add(handler);
    return () => { this.connectionHandlers.delete(handler); };
  }

  disconnect() {
    // Closing a socket fires onclose asynchronously. Disable reconnects before
    // closing so logout/unmount cannot schedule an anonymous retry.
    this.reconnectEnabled = false;
    this.connectedOnce = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.connectTimer) {
      clearTimeout(this.connectTimer);
      this.connectTimer = null;
    }
    const socket = this.ws;
    this.ws = null;
    if (!socket) return;
    if (socket.readyState === WebSocket.CONNECTING) {
      // Closing a CONNECTING socket causes a browser warning. Let the
      // handshake finish silently, then close the established connection.
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      socket.onopen = () => socket.close();
      return;
    }
    socket.close();
  }

  destroy() {
    this.disconnect();
    this.handlers.clear();
    this.connectionHandlers.clear();
  }
}

export const wsClient = new WSClient();
