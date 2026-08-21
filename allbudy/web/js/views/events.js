/** Journal des evenements. */
import { api } from '../api.js';
import { on } from '../store.js';
import { clear, confirmDialog, el, emptyState, formatDate, run, toastError } from '../ui.js';

const LEVEL_CLASS = { info: 'idle', warning: 'paused', error: 'error' };

export function eventsView() {
  const table = el('div', { class: 'card' });
  const levelSelect = el('select', { style: 'max-width:160px' }, [
    el('option', { value: '', text: 'Tous les niveaux' }),
    el('option', { value: 'info', text: 'Information' }),
    el('option', { value: 'warning', text: 'Avertissement' }),
    el('option', { value: 'error', text: 'Erreur' }),
  ]);
  const categorySelect = el('select', { style: 'max-width:160px' }, [
    el('option', { value: '', text: 'Toutes les categories' }),
    ...['printer', 'job', 'file', 'storage', 'queue', 'system'].map((c) =>
      el('option', { value: c, text: c })),
  ]);

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Journal' }),
      el('div', { class: 'spacer' }),
      levelSelect,
      categorySelect,
      el('button', { text: 'Actualiser', onClick: refresh }),
      el('button', {
        class: 'danger', text: 'Vider',
        onClick: () => confirmDialog('Effacer tout le journal ?', async () => {
          await run(() => api.del('api/system/events?keep_days=0'));
          refresh();
        }, { submitLabel: 'Effacer' }),
      }),
    ]),
    table,
  ]);

  levelSelect.addEventListener('change', refresh);
  categorySelect.addEventListener('change', refresh);

  async function refresh() {
    try {
      const params = new URLSearchParams({ limit: '250' });
      if (levelSelect.value) params.set('level', levelSelect.value);
      if (categorySelect.value) params.set('category', categorySelect.value);
      const events = await api.events(`?${params}`);
      clear(table);
      if (!events.length) {
        table.append(emptyState('📜', 'Aucun evenement enregistre.'));
        return;
      }
      const tbody = el('tbody', {}, events.map((event) => el('tr', {}, [
        el('td', { class: 'small muted nowrap', text: formatDate(event.ts) }),
        el('td', {}, [el('span', { class: `badge ${LEVEL_CLASS[event.level] || ''}`, text: event.level })]),
        el('td', { class: 'small muted', text: event.category }),
        el('td', { text: event.message }),
      ])));
      table.append(el('div', { class: 'table-wrap' }, [el('table', {}, [
        el('thead', {}, [el('tr', {}, [
          el('th', { text: 'Date' }), el('th', { text: 'Niveau' }),
          el('th', { text: 'Categorie' }), el('th', { text: 'Message' }),
        ])]),
        tbody,
      ])]));
    } catch (error) {
      toastError(error);
    }
  }

  // Un evenement pousse par le serveur rafraichit la liste, au plus une fois/2 s.
  let pending = null;
  const unsubscribe = on('event', () => {
    if (pending) return;
    pending = setTimeout(() => { pending = null; refresh(); }, 2000);
  });
  root.cleanup = () => {
    unsubscribe();
    clearTimeout(pending);
  };

  refresh();
  return root;
}
