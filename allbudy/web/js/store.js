/**
 * Etat partage du client.
 *
 * Le WebSocket alimente le cache d'etat des imprimantes; les vues s'abonnent
 * aux changements plutot que d'interroger l'API en boucle.
 */
import { BASE, getToken } from './api.js';

const listeners = new Map();

export const state = {
  connected: false,
  printers: new Map(), // printer_id -> payload d'etat temps reel
  user: null,
  info: null,
};

export function on(event, handler) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(handler);
  return () => listeners.get(event).delete(handler);
}

export function emit(event, payload) {
  for (const handler of listeners.get(event) || []) {
    try {
      handler(payload);
    } catch (error) {
      console.error('Erreur dans un abonne', event, error);
    }
  }
}

export function printerState(id) {
  return state.printers.get(Number(id)) || null;
}

let socket = null;
let retryDelay = 1000;
let reconnectTimer = null;

function wsUrl() {
  const url = new URL('ws', BASE);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getToken();
  if (token) url.searchParams.set('token', token);
  return url.toString();
}

export function connectSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  clearTimeout(reconnectTimer);
  socket = new WebSocket(wsUrl());

  socket.addEventListener('open', () => {
    retryDelay = 1000;
    state.connected = true;
    emit('connection', true);
  });

  socket.addEventListener('message', (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    handleMessage(message);
  });

  socket.addEventListener('close', () => {
    state.connected = false;
    emit('connection', false);
    // Reconnexion avec temporisation croissante, plafonnee a 15 s.
    reconnectTimer = setTimeout(connectSocket, retryDelay);
    retryDelay = Math.min(retryDelay * 1.8, 15000);
  });

  socket.addEventListener('error', () => socket.close());
}

export function disconnectSocket() {
  clearTimeout(reconnectTimer);
  if (socket) {
    socket.onclose = null;
    socket.close();
    socket = null;
  }
  state.connected = false;
}

function handleMessage(message) {
  const { type, data } = message;
  switch (type) {
    case 'snapshot':
      state.printers.clear();
      for (const printer of data.printers || []) {
        state.printers.set(printer.printer_id, printer);
      }
      emit('printers', [...state.printers.values()]);
      break;
    case 'printer.status':
      state.printers.set(data.printer_id, data);
      emit('printer', data);
      emit('printers', [...state.printers.values()]);
      break;
    case 'ping':
      break;
    default:
      emit(type, data);
      emit('any', message);
  }
}

export function printerList() {
  return [...state.printers.values()];
}
