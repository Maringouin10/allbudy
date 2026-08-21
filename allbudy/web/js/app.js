/** Shell de l'application: authentification, navigation, routage par hash. */
import { api, setToken } from './api.js';
import { connectSocket, disconnectSocket, on, state } from './store.js';
import { clear, el, field, toast, toastError } from './ui.js';
import { dashboardView } from './views/dashboard.js';
import { eventsView } from './views/events.js';
import { filesView } from './views/files.js';
import { printerDetailView } from './views/printer.js';
import { printersView } from './views/printers.js';
import { queueView } from './views/queue.js';
import { settingsView } from './views/settings.js';
import { spoolsView } from './views/spools.js';
import { storageView } from './views/storage.js';

const NAV = [
  ['dashboard', '◧', 'Tableau de bord'],
  ['printers', '🖨', 'Imprimantes'],
  ['queue', '📋', 'File d\'attente'],
  ['files', '📂', 'Fichiers'],
  ['spools', '🧵', 'Filaments'],
  ['storage', '🗄', 'Depots'],
  ['events', '📜', 'Journal'],
  ['settings', '⚙', 'Reglages'],
];

const app = document.getElementById('app');
let currentView = null;

function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash || 'dashboard';
}

function navigate(route) {
  window.location.hash = `#/${route}`;
}

function buildView(route) {
  const [section, argument] = route.split('/');
  switch (section) {
    case 'printers': return printersView(navigate);
    case 'printer': return printerDetailView(argument, navigate);
    case 'queue': return queueView(navigate);
    case 'files': return filesView(navigate);
    case 'spools': return spoolsView(navigate);
    case 'storage': return storageView(navigate);
    case 'events': return eventsView(navigate);
    case 'settings': return settingsView(navigate);
    default: return dashboardView(navigate);
  }
}

function renderShell() {
  clear(app);

  const connectionState = el('div', { class: 'conn-state' }, [
    el('span', { class: 'led' }),
    el('span', { text: 'connexion...' }),
  ]);

  const navButtons = new Map();
  const sidebar = el('nav', { class: 'sidebar' }, [
    el('div', { class: 'brand' }, [
      el('span', { class: 'dot' }),
      el('div', {}, [
        'AllBudy',
        el('small', { text: 'Ferme Creality' }),
      ]),
    ]),
    ...NAV.map(([route, icon, label]) => {
      const button = el('button', { class: 'nav-item', onClick: () => navigate(route) }, [
        el('span', { class: 'icon', text: icon }),
        el('span', { class: 'label', text: label }),
      ]);
      navButtons.set(route, button);
      return button;
    }),
    el('div', { class: 'nav-spacer' }),
    connectionState,
  ]);

  const main = el('main', { class: 'main' });
  app.append(sidebar, main);

  function updateConnection(connected) {
    connectionState.classList.toggle('live', connected);
    connectionState.lastChild.textContent = connected ? 'temps reel actif' : 'reconnexion...';
  }
  on('connection', updateConnection);
  updateConnection(state.connected);

  function render() {
    const route = currentRoute();
    const section = route.split('/')[0];
    for (const [key, button] of navButtons) {
      const active = key === section || (section === 'printer' && key === 'printers');
      button.classList.toggle('active', active);
    }
    if (currentView && typeof currentView.cleanup === 'function') {
      currentView.cleanup();
    }
    clear(main);
    try {
      currentView = buildView(route);
      main.append(currentView);
    } catch (error) {
      toastError(error);
      main.append(el('p', { text: 'Impossible d\'afficher cette page.' }));
    }
  }

  window.addEventListener('hashchange', render);
  render();
}

function renderLogin() {
  clear(app);
  const username = el('input', { value: 'admin', autocomplete: 'username' });
  const password = el('input', { type: 'password', autocomplete: 'current-password' });
  const error = el('div', { class: 'small', style: 'color:var(--err);min-height:1.2em' });

  const submit = async () => {
    error.textContent = '';
    try {
      const result = await api.login(username.value, password.value);
      setToken(result.access_token);
      await start();
    } catch (exception) {
      error.textContent = exception.message;
    }
  };

  for (const input of [username, password]) {
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });
  }

  app.append(el('div', { class: 'login-wrap' }, [
    el('div', { class: 'card login-box' }, [
      el('div', { class: 'brand', style: 'justify-content:center' }, [
        el('span', { class: 'dot' }),
        el('div', {}, ['AllBudy', el('small', { text: 'Ferme d\'impression Creality' })]),
      ]),
      field('Identifiant', username),
      field('Mot de passe', password),
      error,
      el('button', { class: 'primary', style: 'width:100%', text: 'Se connecter', onClick: submit }),
      el('p', { class: 'small muted', style: 'margin-top:1rem;text-align:center', text: 'Identifiants par defaut: admin / allbudy' }),
    ]),
  ]));
  password.focus();
}

async function start() {
  try {
    state.info = await api.info();
  } catch (error) {
    toastError(error);
    return;
  }

  if (state.info.auth_enabled) {
    try {
      state.user = await api.me();
    } catch {
      disconnectSocket();
      renderLogin();
      return;
    }
  }

  connectSocket();
  renderShell();
  if (!window.location.hash) navigate('dashboard');
}

window.addEventListener('allbudy:unauthorized', () => {
  disconnectSocket();
  renderLogin();
  toast('Session expiree, reconnectez-vous', 'warn');
});

start();
