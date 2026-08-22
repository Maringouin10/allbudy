/** Shell de l'application: navigation et routage par hash. */
import { api } from './api.js';
import { connectSocket, on, state } from './store.js';
import { clear, el, toastError } from './ui.js';
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

async function start() {
  try {
    state.info = await api.info();
  } catch (error) {
    toastError(error);
    return;
  }

  // Sans authentification, l'API renvoie un compte local implicite: aucun
  // ecran de connexion n'est necessaire pour une instance sur reseau local.
  if (state.info.auth_enabled) {
    try {
      state.user = await api.me();
    } catch (error) {
      toastError(error);
    }
  }

  connectSocket();
  renderShell();
  if (!window.location.hash) navigate('dashboard');
}

start();
