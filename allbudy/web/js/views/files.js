/** Bibliotheque de fichiers tranches. */
import { api, apiUrl } from '../api.js';
import { materialChip } from '../components.js';
import { printNowModal } from './printer.js';
import {
  clear, confirmDialog, el, emptyState, field, formatBytes, formatDate, formatDuration, modal,
  run, toast, toastError,
} from '../ui.js';

function fileMetaLines(file) {
  const meta = file.meta || {};
  const lines = [];
  if (meta.print_time_s) lines.push(`⏱ ${formatDuration(meta.print_time_s)}`);
  if (meta.filament_used_g) lines.push(`⚖ ${Math.round(meta.filament_used_g)} g`);
  if (meta.layer_count) lines.push(`▤ ${meta.layer_count} couches`);
  if (meta.nozzle_diameter) lines.push(`⌀ ${meta.nozzle_diameter} mm`);
  return lines;
}

function filamentChips(file) {
  const meta = file.meta || {};
  const types = meta.filament_types || [];
  const colors = meta.filament_colors || [];
  const count = Math.max(types.length, colors.length);
  if (!count) return null;
  return el('div', { class: 'row', style: 'gap:.25rem' },
    Array.from({ length: count }, (_, i) =>
      materialChip(types[i] || '?', colors[i] || '#7f8c8d')));
}

export function filesView() {
  const grid = el('div', { class: 'grid files' });
  const searchInput = el('input', { placeholder: 'Rechercher un fichier...', style: 'max-width:260px' });
  const dropzone = el('div', { class: 'dropzone', text: 'Deposez vos .gcode / .3mf ici, ou cliquez pour choisir' });
  const fileInput = el('input', { type: 'file', accept: '.gcode,.gco,.g,.3mf', multiple: true, style: 'display:none' });
  const counter = el('div', { class: 'small muted' });

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Fichiers' }),
      el('div', { class: 'spacer' }),
      searchInput,
      el('button', { text: 'Actualiser', onClick: () => refresh() }),
    ]),
    dropzone,
    fileInput,
    counter,
    grid,
  ]);

  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refresh, 250);
  });

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (event) => {
    event.preventDefault();
    dropzone.classList.add('over');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('over'));
  dropzone.addEventListener('drop', (event) => {
    event.preventDefault();
    dropzone.classList.remove('over');
    uploadAll(event.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => {
    uploadAll(fileInput.files);
    fileInput.value = '';
  });

  async function uploadAll(fileList) {
    const items = [...fileList];
    if (!items.length) return;
    for (const item of items) {
      const formData = new FormData();
      formData.append('file', item);
      dropzone.textContent = `Envoi de ${item.name}...`;
      try {
        const result = await api.upload('api/files', formData);
        toast(result.created ? `${item.name} importe` : `${item.name} etait deja present`,
          result.created ? 'ok' : 'info');
      } catch (error) {
        toastError(error);
      }
    }
    dropzone.textContent = 'Deposez vos .gcode / .3mf ici, ou cliquez pour choisir';
    refresh();
  }

  function fileCard(file) {
    const meta = file.meta || {};
    const thumb = el('div', { class: 'file-thumb' }, [
      file.thumbnail
        ? el('img', { src: apiUrl(`api/files/${file.id}/thumbnail`), alt: '', loading: 'lazy' })
        : el('div', { class: 'ph', text: file.kind === '3mf' ? '📦' : '🧩' }),
    ]);

    const actions = el('div', { class: 'row', style: 'gap:.3rem' }, [
      el('button', { class: 'sm primary', text: 'File', title: 'Ajouter a la file d\'attente', onClick: () => queueModal(file) }),
      el('button', { class: 'sm', text: 'Imprimer', onClick: () => printNowModal(file, refresh) }),
      el('button', { class: 'sm ghost', text: '⋯', onClick: () => detailsModal(file) }),
    ]);

    return el('div', { class: 'card file-card' }, [
      thumb,
      el('div', { class: 'truncate', title: file.filename, text: file.filename }),
      filamentChips(file),
      el('div', { class: 'small muted', text: fileMetaLines(file).join('  ') || formatBytes(file.size) }),
      !file.printable
        ? el('div', { class: 'badge', style: 'color:var(--warn)', text: '3MF non tranche' })
        : null,
      actions,
    ]);
  }

  function detailsModal(file) {
    const meta = file.meta || {};
    modal({
      title: file.filename,
      submitLabel: null,
      render: (body) => {
        const rows = [
          ['Taille', formatBytes(file.size)],
          ['Type', file.kind === '3mf' ? '3MF' : 'G-code'],
          ['Imprimable', file.printable ? 'oui' : 'non (projet a trancher)'],
          ['Trancheur', meta.slicer || '—'],
          ['Temps estime', meta.print_time_s ? formatDuration(meta.print_time_s) : '—'],
          ['Filament', meta.filament_used_g ? `${Math.round(meta.filament_used_g)} g` : '—'],
          ['Couches', meta.layer_count || '—'],
          ['Hauteur de couche', meta.layer_height ? `${meta.layer_height} mm` : '—'],
          ['Buse', meta.nozzle_diameter ? `${meta.nozzle_diameter} mm` : '—'],
          ['Materiaux', (meta.filament_types || []).join(', ') || '—'],
          ['Machine cible', meta.printer_model || '—'],
          ['Source', file.source === 'remote' ? `depot distant (${file.remote_path || ''})` : 'televersement'],
          ['Ajoute le', formatDate(file.created_at)],
          ['SHA-256', (file.sha256 || '').slice(0, 16) + '…'],
        ];
        const table = el('table', {}, [el('tbody', {}, rows.map(([key, value]) =>
          el('tr', {}, [el('th', { text: key }), el('td', { text: String(value) })])))]);
        body.append(table, el('div', { class: 'row', style: 'margin-top:1rem' }, [
          el('a', { class: 'btn', href: apiUrl(`api/files/${file.id}/download`), text: 'Telecharger', download: file.filename }),
          el('button', {
            class: 'sm', text: 'Re-analyser',
            onClick: () => run(async () => { await api.post(`api/files/${file.id}/reanalyze`); refresh(); }, 'Metadonnees relues'),
          }),
          el('button', {
            class: 'sm danger', text: 'Supprimer',
            onClick: () => confirmDialog(`Supprimer ${file.filename} ?`, async () => {
              await run(() => api.del(`api/files/${file.id}`));
              refresh();
            }, { submitLabel: 'Supprimer' }),
          }),
        ]));
      },
    });
  }

  async function refresh() {
    try {
      const search = searchInput.value.trim();
      const data = await api.files(search ? `?search=${encodeURIComponent(search)}` : '');
      counter.textContent = `${data.total} fichier(s)`;
      clear(grid);
      if (!data.items.length) {
        grid.append(emptyState('📂', search ? 'Aucun resultat.' : 'Aucun fichier. Deposez un G-code pour commencer.'));
        return;
      }
      for (const file of data.items) grid.append(fileCard(file));
    } catch (error) {
      toastError(error);
    }
  }

  refresh();
  return root;
}

/** Modale d'ajout a la file, avec contraintes d'attribution. */
export function queueModal(file, onDone) {
  const meta = file.meta || {};
  // Les champs sont gardes dans la fermeture: les relire par position dans le
  // DOM casserait au moindre changement de mise en page.
  const copies = el('input', { type: 'number', min: 1, value: 1 });
  const priority = el('input', { type: 'number', value: 0 });
  const material = el('input', { value: (meta.filament_types || [])[0] || '', placeholder: 'PLA, PETG, ABS...' });
  const color = el('input', { type: 'color', value: (meta.filament_colors || [])[0] || '#7f8c8d' });
  const useColor = el('input', { type: 'checkbox', checked: !!(meta.filament_colors || [])[0] });
  const tolerance = el('input', { type: 'range', min: 0, max: 160, value: 40 });
  const nozzle = el('input', { type: 'number', step: '0.05', value: meta.nozzle_diameter || '' });
  const tags = el('input', { placeholder: 'chambre-chauffee, grande-plaque' });
  const printerSelect = el('select', {}, [el('option', { value: '', text: 'N\'importe laquelle (matching auto)' })]);

  modal({
    title: `Mettre « ${file.filename} » en file`,
    submitLabel: 'Ajouter a la file',
    render: async (body) => {
      body.append(
        el('p', { class: 'small muted', text: 'Le travail partira vers la premiere imprimante libre qui remplit ces conditions.' }),
        el('div', { class: 'field-row' }, [
          field('Exemplaires', copies),
          field('Priorite', priority, 'Plus grand = envoye avant'),
        ]),
        field('Matiere exigee', material, 'Vide = celle du fichier, ou aucune contrainte'),
        el('div', { class: 'field' }, [
          el('div', { class: 'check' }, [useColor, el('label', { text: 'Exiger une couleur precise' })]),
          el('div', { class: 'row', style: 'margin-top:.4rem' }, [
            el('div', { style: 'width:60px' }, [color]),
            el('div', { style: 'flex:1' }, [
              el('label', { text: 'Tolerance de teinte' }),
              tolerance,
            ]),
          ]),
        ]),
        el('div', { class: 'field-row' }, [
          field('Buse exigee (mm)', nozzle),
          field('Etiquettes exigees', tags),
        ]),
        field('Imprimante imposee', printerSelect),
      );
      try {
        for (const printer of await api.printers()) {
          printerSelect.append(el('option', { value: printer.id, text: printer.name }));
        }
      } catch (error) {
        toastError(error);
      }
    },
    onSubmit: async () => {
      await api.post('api/jobs', {
        file_id: file.id,
        copies: Number(copies.value) || 1,
        priority: Number(priority.value) || 0,
        required_material: material.value.trim() || null,
        required_color: useColor.checked ? color.value : null,
        color_tolerance: Number(tolerance.value),
        required_nozzle: nozzle.value ? Number(nozzle.value) : null,
        required_tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        printer_id: printerSelect.value ? Number(printerSelect.value) : null,
      });
      toast('Travail ajoute a la file', 'ok');
      if (onDone) onDone();
      return true;
    },
  });
}
