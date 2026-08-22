/** Client HTTP de l'API AllBudy. */

const TOKEN_KEY = 'allbudy_token';

/**
 * Racine de l'API, deduite de la page: gere le deploiement derriere un prefixe.
 *
 * Calculee depuis `location.pathname` et non `document.baseURI`: ce dernier
 * inclut le fragment `#/...` de la route courante, et le retirer avec un
 * simple "dernier segment" corrompt l'URL des qu'on recharge la page sur une
 * route (ex: `#/printers`) plutot que sur la racine - tous les appels API
 * partent alors vers `/` au lieu de `/api/...` et recoivent du HTML.
 */
export const BASE = location.origin + location.pathname.replace(/[^/]*$/, '');

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || '';
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, { body, raw, headers = {} } = {}) {
  const options = { method, headers: { ...headers } };
  const token = getToken();
  if (token) options.headers.Authorization = `Bearer ${token}`;

  if (body instanceof FormData) {
    options.body = body;
  } else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  const response = await fetch(BASE + path.replace(/^\//, ''), options);
  if (response.status === 401) {
    setToken('');
    // Sans jeton au depart, ce n'est pas une session expiree mais simplement
    // une visite non authentifiee: l'appelant affichera l'ecran de connexion.
    if (token) window.dispatchEvent(new CustomEvent('allbudy:unauthorized'));
    throw new ApiError(token ? 'Session expiree' : 'Authentification requise', 401);
  }
  if (!response.ok) {
    let detail = `Erreur ${response.status}`;
    try {
      const data = await response.json();
      if (data && data.detail) {
        detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      }
    } catch { /* reponse non JSON: on garde le message par defaut */ }
    throw new ApiError(detail, response.status);
  }
  if (raw) return response;
  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  if (type.includes('application/json')) return response.json();
  const text = await response.text();
  if (text.trimStart().startsWith('<')) {
    // Signe qu'on a recu la page HTML de l'appli a la place de JSON (BASE mal
    // calcule, reverse proxy mal configure...): echouer clairement plutot que
    // de renvoyer une chaine que l'appelant traiterait a tort comme des donnees.
    throw new ApiError('Reponse inattendue du serveur (HTML recu a la place de JSON)', response.status);
  }
  return text;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, { body }),
  patch: (path, body) => request('PATCH', path, { body }),
  del: (path) => request('DELETE', path),
  upload: (path, formData) => request('POST', path, { body: formData }),

  // --- raccourcis metier ---
  info: () => request('GET', 'api/system/info'),
  stats: () => request('GET', 'api/system/stats'),
  login: (username, password) =>
    request('POST', 'api/auth/login', { body: { username, password } }),
  me: () => request('GET', 'api/auth/me'),

  printers: () => request('GET', 'api/printers'),
  printerStatus: () => request('GET', 'api/printers/status'),
  printerCommand: (id, action, body) => request('POST', `api/printers/${id}/${action}`, { body }),
  detectPrinterModel: (id) => request('POST', `api/printers/${id}/detect-model`),

  files: (params = '') => request('GET', `api/files${params}`),
  jobs: (params = '') => request('GET', `api/jobs${params}`),
  spoolInventory: () => request('GET', 'api/spools/inventory'),
  storages: () => request('GET', 'api/storage'),
  browseStorage: (id, path) =>
    request('GET', `api/storage/${id}/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  storageTree: (id, depth) => request('GET', `api/storage/${id}/tree${depth ? `?depth=${depth}` : ''}`),
  importFromStorage: (id, path) => request('POST', `api/storage/${id}/import`, { body: { path } }),
  events: (params = '') => request('GET', `api/system/events${params}`),
};

/** URL absolue d'une ressource servie par l'API (miniature, telechargement). */
export function apiUrl(path) {
  return BASE + path.replace(/^\//, '');
}
