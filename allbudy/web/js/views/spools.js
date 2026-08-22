/** Inventaire des filaments: emplacements CFS et bobines externes. */
import { api } from '../api.js';
import { on } from '../store.js';
import {
  clear, confirmDialog, contrastColor, el, field, modal, run, toast, toastError,
} from '../ui.js';

const MATERIALS = ['PLA', 'PLA-CF', 'PETG', 'PETG-CF', 'ABS', 'ASA', 'TPU', 'PA', 'PC', 'HIPS', 'PVA'];

function spoolForm(spool = {}, printers = []) {
  const fields = {
    printer_id: el('select', {}, [
      el('option', { value: '', text: 'Stock (non montee)' }),
      ...printers.map((p) => el('option', {
        value: p.id, text: p.name, selected: spool.printer_id === p.id,
      })),
    ]),
    unit: el('input', { type: 'number', value: spool.unit ?? 0 }),
    slot: el('input', { type: 'number', min: 0, value: spool.slot ?? 0 }),
    material: el('input', { value: spool.material || 'PLA', list: 'materials' }),
    color_hex: el('input', { type: 'color', value: spool.color_hex || '#7F8C8D' }),
    color_name: el('input', { value: spool.color_name || '', placeholder: 'Rouge feu' }),
    vendor: el('input', { value: spool.vendor || '', placeholder: 'Creality, Polymaker...' }),
    remaining_g: el('input', { type: 'number', value: spool.remaining_g ?? '' }),
    total_g: el('input', { type: 'number', value: spool.total_g ?? 1000 }),
    active: el('input', { type: 'checkbox', checked: !!spool.active }),
    empty: el('input', { type: 'checkbox', checked: !!spool.empty }),
  };

  const body = el('div', {}, [
    el('datalist', { id: 'materials' }, MATERIALS.map((m) => el('option', { value: m }))),
    field('Emplacement', fields.printer_id),
    el('div', { class: 'field-row' }, [
      field('Unite CFS', fields.unit, '-1 pour une bobine sur support externe'),
      field('Emplacement', fields.slot, '0 a 3 dans un CFS'),
    ]),
    el('div', { class: 'field-row' }, [
      field('Matiere', fields.material),
      field('Couleur', fields.color_hex),
    ]),
    el('div', { class: 'field-row' }, [
      field('Nom de couleur', fields.color_name),
      field('Marque', fields.vendor),
    ]),
    el('div', { class: 'field-row' }, [
      field('Restant (g)', fields.remaining_g),
      field('Bobine pleine (g)', fields.total_g),
    ]),
    el('div', { class: 'check' }, [fields.active, el('label', { text: 'Filament charge jusqu\'a la buse' })]),
    el('div', { class: 'check' }, [fields.empty, el('label', { text: 'Emplacement vide' })]),
  ]);

  const read = () => ({
    printer_id: fields.printer_id.value ? Number(fields.printer_id.value) : null,
    unit: Number(fields.unit.value) || 0,
    slot: Number(fields.slot.value) || 0,
    material: fields.material.value.trim() || 'PLA',
    color_hex: fields.color_hex.value,
    color_name: fields.color_name.value.trim() || null,
    vendor: fields.vendor.value.trim() || null,
    remaining_g: fields.remaining_g.value ? Number(fields.remaining_g.value) : null,
    total_g: fields.total_g.value ? Number(fields.total_g.value) : null,
    active: fields.active.checked,
    empty: fields.empty.checked,
  });

  return { body, read };
}

export function spoolsView() {
  const container = el('div', { class: 'col' });
  let printers = [];

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Filaments' }),
      el('div', { class: 'spacer' }),
      el('button', { class: 'primary', text: '+ Bobine', onClick: () => openEditor() }),
      el('button', { text: 'Actualiser', onClick: refresh }),
    ]),
    el('p', { class: 'small muted', text: 'Les emplacements CFS sont synchronises automatiquement depuis les machines. Declarez ici les bobines des imprimantes sans CFS pour que la file d\'attente sache quoi leur envoyer.' }),
    container,
  ]);

  function openEditor(spool) {
    const { body, read } = spoolForm(spool || {}, printers);
    modal({
      title: spool ? 'Modifier la bobine' : 'Nouvelle bobine',
      submitLabel: spool ? 'Enregistrer' : 'Ajouter',
      render: (host) => host.append(body),
      onSubmit: async () => {
        const payload = read();
        if (spool) await api.patch(`api/spools/${spool.id}`, payload);
        else await api.post('api/spools', payload);
        toast(spool ? 'Bobine mise a jour' : 'Bobine ajoutee', 'ok');
        refresh();
        return true;
      },
    });
  }

  function spoolRow(spool) {
    const actions = [
      spool.managed ? el('span', { class: 'badge', text: 'CFS' }) : null,
      el('button', { class: 'sm', text: 'Modifier', onClick: () => openEditor(spool) }),
      el('button', {
        class: 'sm danger', text: '✕',
        onClick: () => confirmDialog('Supprimer cette bobine de l\'inventaire ?', async () => {
          await run(() => api.del(`api/spools/${spool.id}`));
          refresh();
        }, { submitLabel: 'Supprimer' }),
      }),
    ];

    // Un emplacement vide n'a rien a montrer (materiau, couleur, pourcentage):
    // une ligne courte plutot que des champs vides ou perimes.
    if (spool.empty) {
      return el('div', { class: 'queue-item', style: 'opacity:.55' }, [
        el('span', {
          class: 'chip',
          style: 'background:var(--panel-2);width:22px;height:22px;border-radius:6px;border:1px solid rgba(255,255,255,.2)',
        }),
        el('div', { class: 'grow small muted', text: `Emplacement ${spool.slot} — vide` }),
        ...actions,
      ]);
    }

    const percent = spool.remaining_g != null && spool.total_g
      ? Math.round((spool.remaining_g / spool.total_g) * 100)
      : null;
    return el('div', { class: 'queue-item' }, [
      el('span', {
        class: 'chip',
        style: `background:${spool.color_hex};width:22px;height:22px;border-radius:6px;border:1px solid rgba(255,255,255,.2)`,
      }),
      el('div', { class: 'grow' }, [
        el('div', {}, [
          el('strong', { text: spool.material }),
          spool.color_name ? el('span', { class: 'small muted', text: ` ${spool.color_name}` }) : null,
          spool.vendor ? el('span', { class: 'small muted', text: ` · ${spool.vendor}` }) : null,
        ]),
        el('div', { class: 'small muted', text: `unite ${spool.unit} · emplacement ${spool.slot}${percent != null ? ` · ${percent} % restant` : ''}` }),
      ]),
      spool.active ? el('span', { class: 'badge printing', text: 'chargee' }) : null,
      el('button', {
        class: 'sm', text: 'Vider', title: 'Marquer cet emplacement comme vide',
        onClick: () => run(async () => {
          await api.patch(`api/spools/${spool.id}`, { empty: true, active: false });
          refresh();
        }, 'Emplacement marque vide'),
      }),
      ...actions,
    ]);
  }

  async function refresh() {
    try {
      [printers] = await Promise.all([api.printers()]);
      const inventory = await api.spoolInventory();
      clear(container);
      for (const group of inventory) {
        const card = el('div', { class: 'card' }, [
          el('div', { class: 'row' }, [
            el('h3', { text: group.printer }),
            group.has_cfs ? el('span', { class: 'badge', text: 'CFS' }) : null,
            el('div', { style: 'flex:1' }),
            el('button', {
              class: 'sm', text: '+ Bobine',
              onClick: () => openEditor({ printer_id: group.printer_id, slot: group.spools.length }),
            }),
          ]),
        ]);
        if (!group.spools.length) {
          card.append(el('p', { class: 'small muted', text: 'Aucune bobine declaree.' }));
        } else {
          for (const spool of group.spools) card.append(spoolRow(spool));
        }
        container.append(card);
      }
    } catch (error) {
      toastError(error);
    }
  }

  const unsubscribe = on('spools.updated', refresh);
  root.cleanup = unsubscribe;

  refresh();
  return root;
}

export { contrastColor };
