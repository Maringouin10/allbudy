/** Tableau de bord: vue d'ensemble du parc. */
import { api } from '../api.js';
import { printerCard, statCard } from '../components.js';
import { on, printerList } from '../store.js';
import { clear, el, emptyState, formatBytes, toastError } from '../ui.js';

export function dashboardView(navigate) {
  const statsRow = el('div', { class: 'grid stats' });
  const printersGrid = el('div', { class: 'grid printers' });

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Tableau de bord' }),
      el('div', { class: 'spacer' }),
      el('button', { text: 'Actualiser', onClick: () => { refreshStats(); renderPrinters(); } }),
    ]),
    statsRow,
    el('h2', { style: 'margin-top:1.5rem', text: 'Parc' }),
    printersGrid,
  ]);

  function renderPrinters() {
    const entries = printerList().sort((a, b) => a.printer_id - b.printer_id);
    clear(printersGrid);
    if (!entries.length) {
      printersGrid.append(emptyState(
        '🖨️',
        'Aucune imprimante configuree.',
        el('button', { class: 'primary', text: 'Ajouter une imprimante', onClick: () => navigate('printers') }),
      ));
      return;
    }
    for (const entry of entries) {
      printersGrid.append(printerCard(entry, {
        onOpen: (id) => navigate(`printer/${id}`),
      }));
    }
  }

  async function refreshStats() {
    try {
      const stats = await api.stats();
      clear(statsRow);
      statsRow.append(
        statCard('Imprimantes en ligne', `${stats.printers.online} / ${stats.printers.total}`,
          `${stats.printers.printing} en impression`),
        statCard('Travaux en file', stats.jobs.queued,
          stats.dispatcher_enabled ? 'Attribution active' : 'Attribution suspendue'),
        statCard('Termines (7 j)', stats.jobs.completed_7d),
        statCard('Bibliotheque', stats.library.files, formatBytes(stats.library.bytes)),
        statCard('Bobines suivies', stats.spools),
        statCard('Disque libre', formatBytes(stats.disk.free), stats.platform),
      );
    } catch (error) {
      toastError(error);
    }
  }

  const unsubscribe = on('printers', renderPrinters);
  root.cleanup = () => {
    unsubscribe();
    clearInterval(timer);
  };

  renderPrinters();
  refreshStats();
  const timer = setInterval(refreshStats, 20000);
  return root;
}
