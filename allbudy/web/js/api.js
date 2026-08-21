/** Client HTTP de l'API AllBudy. */

const TOKEN_KEY = 'allbudy_token';

/** Racine de l'API, deduite de la page: gere le deploiement derriere un prefixe. */
export const BASE = document.baseURI.replace(/\/[^/]*$/, '/');

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
  return type.includes('application/json') ? response.json() : response.text();
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

  files: (params = '') => request('GET', `api/files${params}`),
  jobs: (params = '') => request('GET', `api/jobs${params}`),
  spoolInventory: () => request('GET', 'api/spools/inventory'),
  storages: () => request('GET', 'api/storage'),
  events: (params = '') => request('GET', `api/system/events${params}`),
};

/** URL absolue d'une ressource servie par l'API (miniature, telechargement). */
export function apiUrl(path) {
  return BASE + path.replace(/^\//, '');
}
