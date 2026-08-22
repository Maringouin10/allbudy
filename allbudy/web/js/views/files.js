/** Bibliotheque de fichiers tranches. */
import { api, apiUrl } from '../api.js';
import { materialChip, printerIcon } from '../components.js';
import { printerState } from '../store.js';
import {
  badge, clear, confirmDialog, el, emptyState, field, formatBytes, formatDate, formatDuration,
  modal, run, toast, toastError,
} from '../ui.js';

function fileMetaLines(file) {
  const meta = file.meta || {};
  const lines = [];
  if (meta.print_time_s) lines.push(`⏱ ${formatDuration(meta.print_time_s)}`);
  if (meta.filament_used_g) lines.push(`⚖ ${Math.round(meta.filament_used_g)} g`);
  if (meta.layer_count) lines.push(`▤ ${meta.layer_count} couches`);
  if (meta.object_count) lines.push(`⬛ ${meta.object_count} piece(s)`);
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

/** Icone pour une entree de depot distant, selon son type/extension. */
function entryIcon(entry) {
  if (entry.is_dir) return '📁';
  return /\.(gcode|gco|g)$/i.test(entry.name) ? '🧩' : /\.3mf$/i.test(entry.name) ? '📦' : '📄';
}

export function filesView() {
  const grid = el('div', { class: 'grid files' });
  const searchInput = el('input', { placeholder: 'Rechercher un fichier...', style: 'max-width:260px' });
  const dropzone = el('div', { class: 'dropzone', text: 'Deposez vos .gcode / .3mf ici, ou cliquez pour choisir' });
  const fileInput = el('input', { type: 'file', accept: '.gcode,.gco,.g,.3mf', multiple: true, style: 'display:none' });
  const counter = el('div', { class: 'small muted' });

  const libraryHead = el('div', { class: 'page-head' }, [
    el('h1', { text: 'Fichiers' }),
    el('div', { class: 'spacer' }),
    searchInput,
    el('button', { text: 'Actualiser', onClick: () => refresh() }),
  ]);
  const libraryPane = el('div', {}, [libraryHead, dropzone, fileInput, counter, grid]);
  const browsePane = el('div', { style: 'display:none' });

  /** Barre laterale « sources »: bibliotheque locale + depots distants (a la Bambuddy). */
  let storages = [];
  let activeSource = 'library';
  let activePath = null;
  // Arborescence pre-chargee de chaque depot (2-3 niveaux): les sous-dossiers
  // profonds sont donc directement cliquables depuis la barre laterale, sans
  // ouvrir les dossiers intermediaires un par un.
  const storageTrees = new Map();
  const sourceList = el('div', { class: 'col', style: 'gap:.2rem' });

  function sourceButton(key, icon, label, { active, indent = 0, onClick } = {}) {
    return el('button', {
      class: 'sm ghost',
      style: `justify-content:flex-start;width:100%;text-align:left;padding-left:${0.6 + indent * 0.9}rem;${active ? 'background:var(--accent-soft);color:var(--accent);font-weight:600' : ''}`,
      onClick,
    }, [`${icon} ${label}`]);
  }

  function folderButtons(storage, node, depth) {
    const buttons = [];
    for (const child of node.children || []) {
      buttons.push(sourceButton(child.path, '📁', child.name, {
        active: activeSource === storage.id && activePath === child.path,
        indent: depth,
        onClick: () => showStorage(storage, child.path),
      }));
      buttons.push(...folderButtons(storage, child, depth + 1));
    }
    return buttons;
  }

  function renderSidebar() {
    clear(sourceList);
    sourceList.append(sourceButton('library', '📂', 'Bibliotheque', {
      active: activeSource === 'library',
      onClick: showLibrary,
    }));
    for (const storage of storages) {
      sourceList.append(sourceButton(storage.id, '🗄', storage.name, {
        active: activeSource === storage.id && !activePath,
        onClick: () => showStorage(storage),
      }));
      const tree = storageTrees.get(storage.id);
      if (tree) for (const button of folderButtons(storage, tree, 1)) sourceList.append(button);
    }
  }

  async function loadStorages() {
    try {
      storages = await api.storages();
      renderSidebar();
    } catch (error) {
      console.warn('Depots distants indisponibles', error);
      return;
    }
    // Chaque arborescence se charge independamment: un depot injoignable ne
    // doit pas retarder l'affichage des autres.
    await Promise.all(storages.map(async (storage) => {
      try {
        storageTrees.set(storage.id, await api.storageTree(storage.id, 3));
      } catch (error) {
        console.warn(`Arborescence indisponible pour ${storage.name}`, error);
      } finally {
        renderSidebar();
      }
    }));
  }

  function showLibrary() {
    activeSource = 'library';
    activePath = null;
    renderSidebar();
    libraryPane.style.display = '';
    browsePane.style.display = 'none';
  }

  function showStorage(storage, path) {
    if (!storage) return;
    activeSource = storage.id;
    activePath = path || null;
    renderSidebar();
    libraryPane.style.display = 'none';
    browsePane.style.display = '';
    renderBrowse(storage, path || null);
  }

  function breadcrumb(storage, path) {
    const root = storage.remote_path || '/';
    const relative = (path || root).slice(root.length).split('/').filter(Boolean);
    const crumbs = [el('a', { href: '#', text: storage.name, onClick: (event) => { event.preventDefault(); renderBrowse(storage, root); } })];
    let acc = root;
    for (const part of relative) {
      acc = acc.endsWith('/') ? `${acc}${part}` : `${acc}/${part}`;
      const target = acc;
      crumbs.push(el('span', { class: 'muted', text: ' / ' }));
      crumbs.push(el('a', { href: '#', text: part, onClick: (event) => { event.preventDefault(); renderBrowse(storage, target); } }));
    }
    return el('div', { class: 'small', style: 'margin-bottom:.6rem' }, crumbs);
  }

  async function renderBrowse(storage, path) {
    clear(browsePane).append(
      el('div', { class: 'page-head' }, [
        el('h1', { text: storage.name }),
        el('div', { class: 'spacer' }),
        el('button', { text: 'Actualiser', onClick: () => renderBrowse(storage, path) }),
      ]),
      el('p', { class: 'small muted', text: 'Parcourez le depot et importez les fichiers voulus dans la bibliotheque.' }),
      el('div', { class: 'card' }, [el('p', { class: 'small muted', text: 'Chargement...' })]),
    );
    try {
      const data = await api.browseStorage(storage.id, path);
      activePath = data.path === (storage.remote_path || '/') ? null : data.path;
      renderSidebar();
      const card = el('div', { class: 'col' });
      card.append(breadcrumb(storage, data.path));
      if (!data.entries.length) {
        card.append(emptyState('🗄️', 'Dossier vide.'));
      } else {
        for (const entry of data.entries) {
          card.append(el('div', { class: 'queue-item' }, [
            el('span', { text: entryIcon(entry) }),
            el('div', {
              class: 'grow truncate',
              text: entry.name,
              style: entry.is_dir ? 'cursor:pointer;font-weight:600' : '',
              onClick: entry.is_dir ? () => renderBrowse(storage, entry.path) : null,
            }),
            !entry.is_dir ? el('span', { class: 'small muted nowrap', text: formatBytes(entry.size) }) : null,
            !entry.is_dir ? el('span', { class: 'small muted nowrap', text: entry.modified ? formatDate(entry.modified) : '' }) : null,
            entry.is_dir
              ? el('button', { class: 'sm', text: 'Ouvrir', onClick: () => renderBrowse(storage, entry.path) })
              : el('button', {
                class: `sm ${entry.imported ? '' : 'primary'}`,
                text: entry.imported ? 'Deja importe' : 'Importer',
                disabled: entry.imported,
                onClick: () => run(async () => {
                  await api.importFromStorage(storage.id, entry.path);
                  renderBrowse(storage, data.path);
                }, `${entry.name} importe`),
              }),
          ]));
        }
      }
      clear(browsePane).append(
        el('div', { class: 'page-head' }, [
          el('h1', { text: storage.name }),
          el('div', { class: 'spacer' }),
          el('button', { text: 'Actualiser', onClick: () => renderBrowse(storage, data.path) }),
        ]),
        el('p', { class: 'small muted', text: 'Parcourez le depot et importez les fichiers voulus dans la bibliotheque.' }),
        el('div', { class: 'card' }, [card]),
      );
    } catch (error) {
      toastError(error);
    }
  }

  const root = el('div', { class: 'row', style: 'align-items:flex-start;gap:1.2rem' }, [
    el('div', { class: 'card', style: 'width:220px;flex:none' }, [
      el('h3', { text: 'Sources' }),
      sourceList,
    ]),
    el('div', { style: 'flex:1;min-width:0' }, [libraryPane, browsePane]),
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
      el('button', { class: 'sm primary', text: '🖨 Imprimer', onClick: () => printModal(file, refresh) }),
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
          ['Pieces sur le plateau', meta.object_count || 'non indique par le trancheur'],
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

  renderSidebar();
  refresh();
  loadStorages();
  return root;
}

/**
 * Une entree de selection de bobine pour un emplacement couleur du fichier:
 * « auto » laisse deduire des metadonnees, un choix de bobine fige
 * matiere+couleur sur ce qui a deja ete charge par le passe (plutot que de
 * deviner), « manuel » permet de saisir matiere/couleur a la main quand
 * aucune bobine ne correspond encore dans l'inventaire.
 */
function colorSlotField(index, total, meta, inventory, spoolById) {
  const fileMaterial = (meta.filament_types || [])[index] || '';
  const fileColor = (meta.filament_colors || [])[index] || null;

  // Pastille de previsualisation: on choisit une bobine par sa couleur, pas
  // par son code hexadecimal — inutile de savoir lire un #RRGGBB.
  const dot = el('span', {
    style: `display:inline-block;width:22px;height:22px;border-radius:6px;flex:none;background:${fileColor || '#7f8c8d'};border:1px solid rgba(255,255,255,.3)`,
  });

  const select = el('select', {}, [
    el('option', {
      value: 'auto',
      style: fileColor ? `background:${fileColor}` : null,
      text: `Auto — ${fileMaterial || 'matiere du fichier'}${fileColor ? ' (couleur du fichier)' : ''}`,
    }),
    el('option', { value: 'manual', text: 'Saisie manuelle...' }),
  ]);
  for (const group of inventory) {
    const usable = (group.spools || []).filter((s) => !s.empty);
    if (!usable.length) continue;
    const optgroup = el('optgroup', { label: group.printer });
    for (const spool of usable) {
      const key = `spool-${spool.id}`;
      spoolById.set(key, { material: spool.material, color: spool.color_hex });
      optgroup.append(el('option', {
        value: key,
        style: `background:${spool.color_hex}`,
        text: `${spool.material} · ${spool.color_name || spool.color_hex}${spool.vendor ? ` · ${spool.vendor}` : ''}`,
      }));
    }
    select.append(optgroup);
  }

  const manualMaterial = el('input', { value: fileMaterial, placeholder: 'PLA, PETG, ABS...' });
  const manualColor = el('input', { type: 'color', value: fileColor || '#7f8c8d' });
  const manualRow = el('div', { class: 'row', style: 'margin-top:.35rem;display:none' }, [
    el('div', { style: 'flex:1' }, [manualMaterial]),
    manualColor,
  ]);

  const updatePreview = () => {
    if (select.value === 'auto') dot.style.background = fileColor || '#7f8c8d';
    else if (select.value === 'manual') dot.style.background = manualColor.value;
    else dot.style.background = (spoolById.get(select.value) || {}).color || '#7f8c8d';
  };
  select.addEventListener('change', () => {
    manualRow.style.display = select.value === 'manual' ? 'flex' : 'none';
    updatePreview();
  });
  manualColor.addEventListener('input', updatePreview);

  const node = el('div', {}, [
    el('div', { class: 'row', style: 'gap:.5rem;flex-wrap:nowrap' }, [
      dot,
      el('span', { class: 'small nowrap', style: 'min-width:80px', text: total > 1 ? `Couleur ${index + 1}` : 'Bobine' }),
      el('span', { class: 'small muted nowrap', text: fileMaterial || '?' }),
      el('span', { class: 'small muted', text: '→' }),
      el('div', { style: 'flex:1;min-width:0' }, [select]),
    ]),
    manualRow,
  ]);

  const read = () => {
    if (select.value === 'auto') return null;
    if (select.value === 'manual') {
      const material = manualMaterial.value.trim();
      const colorValue = manualColor.value;
      return material || colorValue ? { material: material || null, color: colorValue || null } : null;
    }
    return spoolById.get(select.value) || null;
  };

  return { node, read };
}

function sectionBlock(title, ...content) {
  return el('div', { class: 'card-section' }, [
    el('div', { class: 'section-title', text: title }),
    ...content,
  ]);
}

/** Ligne « carte imprimante » du selecteur de cible. */
function printerPickRow(printer, { selectable, selected, onClick }) {
  const live = printerState(printer.id);
  const connected = !!(live && live.connected);
  const state = connected ? live.status.state : 'offline';
  return el('div', {
    class: 'queue-item',
    style: `${selectable ? 'cursor:pointer;' : ''}border-color:${selected ? 'var(--accent)' : 'var(--line)'}`,
    onClick: selectable ? onClick : null,
  }, [
    printerIcon(printer.model, { size: 30 }),
    el('div', { class: 'grow' }, [
      el('div', {}, [el('strong', { text: printer.name })]),
      el('div', { class: 'small muted', text: `${printer.model || '?'} · ${printer.host}` }),
    ]),
    badge(state),
    selected ? el('span', { style: 'color:var(--accent);font-weight:700', text: '✓' }) : null,
  ]);
}

/** Modale unique « Imprimer »: cible, bobines, options, quand imprimer. */
export function printModal(file, onDone) {
  const meta = file.meta || {};
  // Les champs sont gardes dans la fermeture: les relire par position dans le
  // DOM casserait au moindre changement de mise en page.
  const copies = el('input', { type: 'number', min: 1, value: 1 });
  const nozzle = el('input', { type: 'number', step: '0.05', value: meta.nozzle_diameter || '' });
  const tags = el('input', { placeholder: 'chambre-chauffee, grande-plaque' });
  const filamentsBox = el('div', { class: 'col' }, [el('p', { class: 'small muted', text: 'Chargement des bobines...' })]);
  const spoolById = new Map();
  let colorSlots = [];

  let printers = [];
  let targetMode = 'specific';
  let selectedPrinterId = null;
  let showAllModels = false;
  const targetToggle = el('div', { class: 'row', style: 'gap:.4rem' });
  const printerBox = el('div', { class: 'col', style: 'gap:.4rem' });

  let bedLeveling = false;
  const bedLevelToggle = el('div', { class: 'row', style: 'gap:.4rem' });

  let whenMode = 'asap';
  const whenToggle = el('div', { class: 'row', style: 'gap:.4rem' });
  const scheduleInput = el('input', { type: 'datetime-local' });
  const scheduleRow = el('div', { style: 'margin-top:.5rem;display:none' }, [field('Date et heure', scheduleInput)]);

  /** Normalise pour comparer « K2 Plus » et « k2-plus » sans faux negatif. */
  const normalizeModel = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');

  /** Le fichier lui-meme indique parfois son imprimante cible (metadonnees du trancheur). */
  function fileModelHint() {
    const raw = (meta.printer_model || '').trim();
    if (!raw) return null;
    const norm = normalizeModel(raw);
    const match = printers.find((p) => {
      const pm = normalizeModel(p.model);
      return pm && (pm === norm || pm.includes(norm) || norm.includes(pm));
    });
    return match ? match.model : null;
  }

  function primaryModel() {
    if (selectedPrinterId) {
      const chosen = printers.find((p) => p.id === selectedPrinterId);
      if (chosen) return chosen.model;
    }
    const hinted = fileModelHint();
    if (hinted) return hinted;
    // Un seul modele dans le parc: aucune ambiguite a lever.
    const distinctModels = new Set(printers.map((p) => p.model));
    if (distinctModels.size === 1) return printers[0].model;
    // Plusieurs modeles et aucun indice fiable: deviner serait souvent faux
    // (ex: proposer une Ender-3 V3 pour un fichier tranche pour une K2) —
    // mieux vaut laisser l'utilisateur choisir explicitement.
    return null;
  }

  function renderTargeting() {
    const model = primaryModel();
    clear(targetToggle).append(
      el('button', {
        class: `sm ${targetMode === 'specific' ? 'primary' : ''}`,
        text: '🖨 Imprimante specifique',
        onClick: () => { targetMode = 'specific'; renderTargeting(); },
      }),
      el('button', {
        class: `sm ${targetMode === 'any' ? 'primary' : ''}`,
        text: model ? `👥 N'importe quelle ${model}` : '👥 N\'importe laquelle',
        onClick: () => { targetMode = 'any'; renderTargeting(); },
      }),
    );

    // Sans modele fiable (fichier ambigu, parc heterogene), on montre tout
    // plutot que de filtrer sur une devinette potentiellement fausse. Le
    // matching automatique ne choisit jamais une virtuelle de lui-meme
    // (voir matcher.py): en mode « N'importe laquelle » elle n'a donc rien
    // a faire dans la liste, seul un ciblage explicite l'autorise.
    const group = (showAllModels || !model ? printers : printers.filter((p) => p.model === model))
      .filter((p) => targetMode === 'specific' || p.transport !== 'virtual');
    const hidden = printers.length - group.length;

    clear(printerBox);
    if (!printers.length) {
      printerBox.append(el('p', { class: 'small muted', text: 'Aucune imprimante configuree.' }));
    }
    for (const printer of group) {
      const selected = targetMode === 'specific' ? selectedPrinterId === printer.id : true;
      printerBox.append(printerPickRow(printer, {
        selectable: targetMode === 'specific',
        selected,
        onClick: () => { selectedPrinterId = printer.id; renderTargeting(); },
      }));
    }
    if (hidden > 0 && !showAllModels) {
      printerBox.append(el('div', { class: 'small muted' }, [
        `⚠ ${hidden} autre(s) imprimante(s) masquee(s) (modele different) — `,
        el('a', { href: '#', text: 'tout afficher', onClick: (event) => { event.preventDefault(); showAllModels = true; renderTargeting(); } }),
      ]));
    }
  }

  function renderBedLevel() {
    clear(bedLevelToggle).append(
      el('button', { class: `sm ${!bedLeveling ? 'primary' : ''}`, text: 'Off', onClick: () => { bedLeveling = false; renderBedLevel(); } }),
      el('button', { class: `sm ${bedLeveling ? 'primary' : ''}`, text: 'On', onClick: () => { bedLeveling = true; renderBedLevel(); } }),
    );
  }

  function renderWhen() {
    clear(whenToggle).append(
      el('button', { class: `sm ${whenMode === 'asap' ? 'primary' : ''}`, text: '⏱ Des que possible', onClick: () => { whenMode = 'asap'; renderWhen(); } }),
      el('button', { class: `sm ${whenMode === 'queue' ? 'primary' : ''}`, text: '📋 File d\'attente', onClick: () => { whenMode = 'queue'; renderWhen(); } }),
      el('button', { class: `sm ${whenMode === 'schedule' ? 'primary' : ''}`, text: '📅 Programmer', onClick: () => { whenMode = 'schedule'; renderWhen(); } }),
    );
    scheduleRow.style.display = whenMode === 'schedule' ? 'block' : 'none';
  }

  modal({
    title: `🖨 Imprimer « ${file.filename} »`,
    submitLabel: '🖨 Imprimer',
    wide: true,
    render: async (body) => {
      body.append(
        sectionBlock('Travail', el('div', { class: 'truncate', text: file.filename })),
        sectionBlock('Imprimante',
          meta.printer_model
            ? el('p', { class: 'small muted', style: 'margin:0 0 .5rem', text: `Fichier tranche pour : ${meta.printer_model}` })
            : null,
          targetToggle, printerBox),
        sectionBlock('Bobines exigees',
          el('p', { class: 'small muted', style: 'margin:0 0 .5rem', text: 'Choisissez une bobine deja chargee par le passe pour fixer sa couleur au lieu de deviner, ou laissez « Auto » pour vous fier au fichier.' }),
          filamentsBox),
        sectionBlock('Options d\'impression',
          el('div', {}, [
            el('div', { class: 'small muted', style: 'margin-bottom:.3rem', text: 'Nivellement du plateau avant impression' }),
            bedLevelToggle,
          ])),
        el('div', { class: 'field-row' }, [
          field('Exemplaires', copies),
        ]),
        sectionBlock('Quand imprimer', whenToggle, scheduleRow),
        sectionBlock('Autres criteres',
          el('div', { class: 'field-row' }, [
            field('Buse exigee (mm)', nozzle),
            field('Etiquettes exigees', tags),
          ])),
      );

      renderBedLevel();
      renderWhen();

      try {
        printers = await api.printers();
        const model = primaryModel();
        if (model) {
          const candidates = printers.filter((p) => p.model === model);
          const free = candidates.find((p) => { const live = printerState(p.id); return live && live.connected && live.status.is_free; });
          selectedPrinterId = (free || candidates[0] || {}).id || null;
        } else {
          // Modele ambigu: pas de devinette, l'utilisateur choisit lui-meme.
          selectedPrinterId = null;
        }
      } catch (error) {
        toastError(error);
      }
      renderTargeting();

      try {
        const inventory = await api.spoolInventory();
        const total = Math.max((meta.filament_types || []).length, (meta.filament_colors || []).length, 1);
        colorSlots = Array.from({ length: total }, (_, i) => colorSlotField(i, total, meta, inventory, spoolById));
        clear(filamentsBox);
        for (const slot of colorSlots) filamentsBox.append(slot.node);
      } catch (error) {
        clear(filamentsBox);
        filamentsBox.append(el('p', { class: 'small muted', text: 'Inventaire des bobines indisponible: la couleur se deduira du fichier.' }));
        toastError(error);
      }
    },
    onSubmit: async () => {
      if (!printers.length) {
        toast('Aucune imprimante configuree', 'warn');
        return false;
      }
      let printerId = null;
      let allowedPrinters = [];
      if (targetMode === 'specific') {
        if (!selectedPrinterId) {
          toast('Choisissez une imprimante', 'warn');
          return false;
        }
        printerId = selectedPrinterId;
      } else {
        const model = primaryModel();
        const group = printers.filter((p) => p.model === model);
        if (group.length && group.length < printers.length) allowedPrinters = group.map((p) => p.id);
      }

      let priority = 0;
      let scheduledAt = null;
      if (whenMode === 'asap') priority = 1000;
      if (whenMode === 'schedule') {
        if (!scheduleInput.value) {
          toast('Choisissez une date de programmation', 'warn');
          return false;
        }
        scheduledAt = new Date(scheduleInput.value).toISOString();
      }

      const overrides = colorSlots.map((slot) => slot.read());
      await api.post('api/jobs', {
        file_id: file.id,
        copies: Number(copies.value) || 1,
        priority,
        scheduled_at: scheduledAt,
        bed_leveling: bedLeveling,
        required_filaments: overrides.some(Boolean) ? overrides : [],
        required_nozzle: nozzle.value ? Number(nozzle.value) : null,
        required_tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean),
        printer_id: printerId,
        allowed_printers: allowedPrinters,
      });
      toast('Travail ajoute a la file', 'ok');
      if (onDone) onDone();
      return true;
    },
  });
}
