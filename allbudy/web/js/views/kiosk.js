/**
 * Mode kiosque: interface sobre pour un petit ecran monte pres d'une
 * imprimante (tablette). Deux gestes seulement: imprimer un fichier de la
 * bibliotheque, et marquer un emplacement vide une fois la bobine retiree.
 * Pas de barre laterale ni de tableau de bord: voir app.js pour le shell
 * dedie qui bypasse le chrome habituel sur les routes #/kiosk.
 */
import { api, apiUrl } from '../api.js';
import { printModal } from './files.js';
import { on } from '../store.js';
import { clear, el, emptyState, run, toastError } from '../ui.js';

function kioskFileTile(file, onPrinted) {
  return el('button', { class: 'kiosk-tile', onClick: () => printModal(file, onPrinted) }, [
    el('div', { class: 'kiosk-tile-thumb' }, [
      file.thumbnail
        ? el('img', { src: apiUrl(`api/files/${file.id}/thumbnail`), alt: '', loading: 'lazy' })
        : el('div', { class: 'ph', text: file.kind === '3mf' ? '📦' : '🧩' }),
    ]),
    el('div', { class: 'kiosk-tile-label truncate', text: file.filename }),
  ]);
}

function kioskSpoolTile(spool, onChanged) {
  if (spool.empty) {
    return el('div', { class: 'kiosk-spool empty' }, [
      el('span', { class: 'kiosk-spool-chip' }),
      el('div', { class: 'kiosk-spool-label small muted', text: `Emplacement ${spool.slot} — vide` }),
    ]);
  }
  return el('div', { class: 'kiosk-spool' }, [
    el('span', { class: 'kiosk-spool-chip', style: `background:${spool.color_hex}` }),
    el('div', { class: 'kiosk-spool-label grow' }, [
      el('div', {}, [
        el('strong', { text: spool.material }),
        spool.color_name ? el('span', { class: 'small muted', text: ` ${spool.color_name}` }) : null,
      ]),
      el('div', { class: 'small muted', text: `unite ${spool.unit} · emplacement ${spool.slot}` }),
    ]),
    el('button', {
      class: 'kiosk-empty-btn', text: 'Vider',
      onClick: () => run(async () => {
        await api.patch(`api/spools/${spool.id}`, { empty: true, active: false });
        onChanged();
      }, 'Emplacement marque vide'),
    }),
  ]);
}

export function kioskView(printerIdArg) {
  const scopedPrinterId = printerIdArg ? Number(printerIdArg) : null;
  let tab = 'print';

  const tabsBox = el('div', { class: 'row', style: 'gap:.4rem' });
  const content = el('div', { class: 'kiosk-content' });

  const root = el('div', { class: 'kiosk' }, [
    el('header', { class: 'kiosk-head' }, [
      el('span', { class: 'kiosk-dot' }),
      el('strong', { text: 'AllBudy' }),
      el('span', { class: 'small muted', text: 'kiosque' }),
      el('div', { class: 'spacer' }),
      tabsBox,
    ]),
    content,
  ]);

  function renderTabs() {
    clear(tabsBox).append(
      el('button', {
        class: `kiosk-tab ${tab === 'print' ? 'active' : ''}`,
        text: '🖨 Imprimer',
        onClick: () => { tab = 'print'; renderTabs(); renderContent(); },
      }),
      el('button', {
        class: `kiosk-tab ${tab === 'spools' ? 'active' : ''}`,
        text: '🧵 Bobines',
        onClick: () => { tab = 'spools'; renderTabs(); renderContent(); },
      }),
    );
  }

  async function renderPrintTab() {
    clear(content).append(el('p', { class: 'small muted', text: 'Chargement...' }));
    try {
      const data = await api.files('?limit=200');
      clear(content);
      if (!data.items.length) {
        content.append(emptyState('📂', 'Aucun fichier dans la bibliotheque.'));
        return;
      }
      const grid = el('div', { class: 'kiosk-grid' });
      for (const file of data.items) grid.append(kioskFileTile(file, renderPrintTab));
      content.append(grid);
    } catch (error) {
      toastError(error);
    }
  }

  async function renderSpoolsTab() {
    clear(content).append(el('p', { class: 'small muted', text: 'Chargement...' }));
    try {
      const inventory = await api.spoolInventory();
      const groups = scopedPrinterId
        ? inventory.filter((group) => group.printer_id === scopedPrinterId)
        : inventory;
      clear(content);
      if (!groups.some((group) => group.spools.length)) {
        content.append(emptyState('🧵', 'Aucune bobine declaree.'));
        return;
      }
      for (const group of groups) {
        if (!group.spools.length) continue;
        content.append(el('h2', { class: 'kiosk-group-title', text: group.printer }));
        const grid = el('div', { class: 'kiosk-grid spools' });
        for (const spool of group.spools) grid.append(kioskSpoolTile(spool, renderSpoolsTab));
        content.append(grid);
      }
    } catch (error) {
      toastError(error);
    }
  }

  function renderContent() {
    if (tab === 'print') renderPrintTab();
    else renderSpoolsTab();
  }

  renderTabs();
  renderContent();

  const unsubscribe = on('spools.updated', () => { if (tab === 'spools') renderSpoolsTab(); });
  root.cleanup = unsubscribe;

  return root;
}
