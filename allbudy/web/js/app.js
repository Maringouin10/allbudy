/** Shell de l'application: navigation et routage par hash. */
import { api } from './api.js';
import { connectSocket, on, state } from './store.js';
import { clear, el, toastError } from './ui.js';
import { dashboardView } from './views/dashboard.js';
import { filesView } from './views/files.js';
import { kioskView } from './views/kiosk.js';
import { printerDetailView } from './views/printer.js';
import { printersView } from './views/printers.js';
import { queueView } from './views/queue.js';
import { settingsView } from './views/settings.js';
import { spoolsView } from './views/spools.js';

const NAV = [
  ['dashboard', '◧', 'Tableau de bord'],
  ['printers', '🖨', 'Imprimantes'],
  ['queue', '📋', 'File d\'attente'],
  ['files', '📂', 'Fichiers'],
  ['spools', '🧵', 'Filaments'],
  ['settings', '⚙', 'Reglages'],
];

// Anciennes routes deplacees dans Reglages (favoris/liens existants).
const ROUTE_REDIRECTS = { storage: 'settings', events: 'settings' };

const app = document.getElementById('app');
let currentView = null;

function currentRoute() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return hash || 'dashboard';
}

function isKioskRoute(route) {
  return route.split('/')[0] === 'kiosk';
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
    if (isKioskRoute(route)) {
      // Le mode kiosque a son propre shell, sans barre laterale: on repart
      // de zero plutot que de demonter celui-ci a chaud.
      window.location.reload();
      return;
    }
    if (ROUTE_REDIRECTS[section]) {
      navigate(ROUTE_REDIRECTS[section]);
      return;
    }
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

/**
 * Shell minimal du mode kiosque: aucune barre laterale, aucun tableau de
 * bord, juste la vue kiosque. Pense pour une tablette montee pres d'une
 * imprimante, ouverte une fois pour toutes sur #/kiosk.
 */
function renderKioskShell() {
  clear(app);
  app.classList.add('kiosk-mode');
  let currentView = null;

  function render() {
    const route = currentRoute();
    if (!isKioskRoute(route)) {
      // Sortie du mode kiosque: le shell normal doit se reconstruire.
      window.location.reload();
      return;
    }
    const [, printerId] = route.split('/');
    if (currentView && typeof currentView.cleanup === 'function') {
      currentView.cleanup();
    }
    clear(app);
    try {
      currentView = kioskView(printerId);
      app.append(currentView);
    } catch (error) {
      toastError(error);
      app.append(el('p', { text: 'Impossible d\'afficher le mode kiosque.' }));
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
  if (isKioskRoute(currentRoute())) {
    renderKioskShell();
  } else {
    renderShell();
    if (!window.location.hash) navigate('dashboard');
  }
}

start();
