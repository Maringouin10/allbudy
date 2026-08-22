/** Tableau de bord: vue d'ensemble du parc. */
import { api, apiUrl } from '../api.js';
import { printerCard, statCard } from '../components.js';
import { on, printerList } from '../store.js';
import { clear, el, emptyState, formatBytes, toastError } from '../ui.js';

export function dashboardView(navigate) {
  const statsRow = el('div', { class: 'grid stats' });
  const printersGrid = el('div', { class: 'grid printers' });

  /**
   * Index des fichiers de la bibliotheque par nom envoye a la machine.
   * Il donne la miniature et le nombre de pieces du travail en cours: la
   * machine ne renvoie qu'un nom de fichier.
   */
  let libraryByName = new Map();
  /** Etat persiste (bed_cleared...) par imprimante: absent du flux WebSocket. */
  let printerMetaById = new Map();

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Tableau de bord' }),
      el('div', { class: 'spacer' }),
      el('button', { text: 'Actualiser', onClick: () => { refreshStats(); loadLibrary(); loadPrinterMeta(); } }),
    ]),
    statsRow,
    el('h2', { style: 'margin-top:1.5rem', text: 'Parc' }),
    printersGrid,
  ]);

  function libraryEntry(filename) {
    if (!filename) return {};
    const name = String(filename).split('/').pop();
    const file = libraryByName.get(name);
    if (!file) return {};
    return {
      pieces: (file.meta || {}).object_count || null,
      thumbnail: file.thumbnail ? apiUrl(`api/files/${file.id}/thumbnail`) : null,
    };
  }

  function renderPrinters() {
    const entries = printerList()
      .filter((entry) => entry.transport !== 'virtual')
      .sort((a, b) => a.printer_id - b.printer_id);
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
      const extra = libraryEntry(entry.status && entry.status.filename);
      const meta = printerMetaById.get(entry.printer_id);
      printersGrid.append(printerCard(entry, {
        onOpen: (id) => navigate(`printer/${id}`),
        onRefresh: loadPrinterMeta,
        pieces: extra.pieces,
        thumbnail: extra.thumbnail,
        bedCleared: meta ? meta.bed_cleared : null,
      }));
    }
  }

  async function loadPrinterMeta() {
    try {
      const printers = await api.printers();
      printerMetaById = new Map(printers.map((p) => [p.id, p]));
      renderPrinters();
    } catch (error) {
      // Sans ces metadonnees, les cartes restent affichables sans le badge plateau.
      console.warn('Metadonnees imprimantes indisponibles', error);
    }
  }

  async function loadLibrary() {
    try {
      const data = await api.files('?limit=500');
      libraryByName = new Map(data.items.map((file) => [file.stored_name, file]));
      renderPrinters();
    } catch (error) {
      // Sans la bibliotheque, les cartes restent affichables: on n'alerte pas.
      console.warn('Index de la bibliotheque indisponible', error);
    }
  }

  async function refreshStats() {
    try {
      const stats = await api.stats();
      clear(statsRow);
      statsRow.append(
        statCard('Imprimantes en ligne', `${stats.printers.online} / ${stats.printers.total}`,
          `${stats.printers.printing} en impression`),
        statCard('Imprimantes libres', stats.printers.free ?? 0,
          'prêtes à prendre un travail'),
        statCard('Travaux en file', stats.jobs.queued,
          stats.dispatcher_enabled ? 'Attribution active' : 'Attribution suspendue'),
        statCard('Pieces imprimees (7 j)', stats.jobs.pieces_7d ?? 0,
          `${stats.jobs.completed_7d} plateau(x) termine(s)`),
        statCard('Terminés (7 j)', stats.jobs.completed_7d, 'travaux'),
        statCard('Bibliotheque', stats.library.files, formatBytes(stats.library.bytes)),
      );
    } catch (error) {
      toastError(error);
    }
  }

  const unsubscribers = [
    on('printers', renderPrinters),
    // Un fichier ajoute ou retire change l'index des miniatures.
    on('file.added', loadLibrary),
    on('file.removed', loadLibrary),
  ];
  root.cleanup = () => {
    unsubscribers.forEach((fn) => fn());
    clearInterval(timer);
  };

  renderPrinters();
  refreshStats();
  loadLibrary();
  loadPrinterMeta();
  const timer = setInterval(() => { refreshStats(); loadPrinterMeta(); }, 20000);
  return root;
}
