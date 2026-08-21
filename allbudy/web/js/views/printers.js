/** Gestion du parc: ajout, edition, decouverte reseau. */
import { api } from '../api.js';
import {
  badge, clear, confirmDialog, el, field, emptyState, modal, run, toast, toastError,
} from '../ui.js';
import { printerState } from '../store.js';

const TRANSPORTS = [
  ['moonraker', 'Moonraker / Klipper (recommande)'],
  ['creality_lan', 'LAN Creality (port 9999, firmware stock)'],
  ['simulator', 'Simulateur (demo, sans materiel)'],
];

const DEFAULT_PORTS = { moonraker: 7125, creality_lan: 9999, simulator: 0 };

function printerForm(printer = {}) {
  const form = {};
  const body = el('div');

  form.name = el('input', { value: printer.name || '', placeholder: 'Atelier K1 #1' });
  form.model = el('input', { value: printer.model || 'K1', placeholder: 'K1 Max, K2 Plus, Ender-3 V3...' });
  form.transport = el('select', {}, TRANSPORTS.map(([value, label]) =>
    el('option', { value, text: label, selected: (printer.transport || 'moonraker') === value })));
  form.host = el('input', { value: printer.host || '', placeholder: '192.168.1.42' });
  form.port = el('input', { type: 'number', value: printer.port ?? '', placeholder: '7125' });
  form.api_key = el('input', { value: printer.api_key || '', placeholder: 'Optionnel (Moonraker)' });
  form.nozzle_diameter = el('input', { type: 'number', step: '0.05', value: printer.nozzle_diameter ?? 0.4 });
  form.tags = el('input', { value: (printer.tags || []).join(', '), placeholder: 'abs, chambre-chauffee' });
  form.camera_url = el('input', { value: printer.camera_url || '', placeholder: 'http://192.168.1.42:8080/?action=stream' });
  form.has_cfs = el('input', { type: 'checkbox', checked: !!printer.has_cfs });
  form.enabled = el('input', { type: 'checkbox', checked: printer.enabled !== false });
  form.auto_assign = el('input', { type: 'checkbox', checked: printer.auto_assign !== false });

  // Le port par defaut suit le protocole tant que l'utilisateur n'a rien saisi.
  form.transport.addEventListener('change', () => {
    if (!form.port.value || Object.values(DEFAULT_PORTS).includes(Number(form.port.value))) {
      form.port.value = DEFAULT_PORTS[form.transport.value] ?? '';
    }
  });

  body.append(
    field('Nom', form.name),
    el('div', { class: 'field-row' }, [
      field('Modele', form.model),
      field('Protocole', form.transport),
    ]),
    el('div', { class: 'field-row' }, [
      field('Adresse IP / hote', form.host),
      field('Port', form.port, 'Vide = port par defaut du protocole'),
    ]),
    field('Cle API Moonraker', form.api_key, 'Seulement si Moonraker est protege par une cle'),
    el('div', { class: 'field-row' }, [
      field('Diametre de buse (mm)', form.nozzle_diameter),
      field('Etiquettes', form.tags, 'Separees par des virgules'),
    ]),
    field('Flux camera', form.camera_url, 'URL MJPEG lue directement par le navigateur'),
    el('div', { class: 'col' }, [
      el('div', { class: 'check' }, [form.has_cfs, el('label', { text: 'Equipee d\'un CFS' })]),
      el('div', { class: 'check' }, [form.enabled, el('label', { text: 'Activee' })]),
      el('div', { class: 'check' }, [form.auto_assign, el('label', { text: 'Accepte les travaux de la file' })]),
    ]),
  );

  const read = () => ({
    name: form.name.value.trim(),
    model: form.model.value.trim() || 'K1',
    transport: form.transport.value,
    host: form.host.value.trim() || '127.0.0.1',
    port: form.port.value ? Number(form.port.value) : null,
    api_key: form.api_key.value.trim() || null,
    nozzle_diameter: Number(form.nozzle_diameter.value) || 0.4,
    tags: form.tags.value.split(',').map((t) => t.trim()).filter(Boolean),
    camera_url: form.camera_url.value.trim() || null,
    has_cfs: form.has_cfs.checked,
    enabled: form.enabled.checked,
    auto_assign: form.auto_assign.checked,
  });

  return { body, read };
}

export function printersView(navigate) {
  const list = el('div', { class: 'card' });
  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'Imprimantes' }),
      el('div', { class: 'spacer' }),
      el('button', { text: 'Rechercher sur le reseau', onClick: openDiscovery }),
      el('button', { class: 'primary', text: '+ Ajouter', onClick: () => openEditor() }),
    ]),
    list,
  ]);

  async function refresh() {
    try {
      const printers = await api.printers();
      clear(list);
      if (!printers.length) {
        list.append(emptyState('🖨️', 'Aucune imprimante. Lancez une recherche reseau pour commencer.'));
        return;
      }
      const table = el('table', {}, [
        el('thead', {}, [el('tr', {}, [
          el('th', { text: 'Nom' }), el('th', { text: 'Modele' }), el('th', { text: 'Protocole' }),
          el('th', { text: 'Adresse' }), el('th', { text: 'Buse' }), el('th', { text: 'Etat' }),
          el('th', {}),
        ])]),
      ]);
      const tbody = el('tbody');
      for (const printer of printers) {
        const live = printerState(printer.id);
        const state = live && live.connected ? live.status.state : 'offline';
        tbody.append(el('tr', {}, [
          el('td', {}, [
            el('a', { href: `#/printer/${printer.id}`, text: printer.name }),
            printer.enabled ? null : el('span', { class: 'badge', text: 'desactivee', style: 'margin-left:.4rem' }),
          ]),
          el('td', { text: printer.model }),
          el('td', { class: 'small muted', text: printer.transport }),
          el('td', { class: 'mono small', text: `${printer.host}:${printer.port}` }),
          el('td', { text: `${printer.nozzle_diameter} mm` }),
          el('td', {}, [badge(state)]),
          el('td', { class: 'actions' }, [
            el('button', { class: 'sm', text: 'Piloter', onClick: () => navigate(`printer/${printer.id}`) }),
            el('button', { class: 'sm', text: 'Modifier', onClick: () => openEditor(printer), style: 'margin-left:.3rem' }),
            el('button', {
              class: 'sm danger', text: 'Suppr.', style: 'margin-left:.3rem',
              onClick: () => confirmDialog(`Supprimer ${printer.name} ?`, async () => {
                await run(() => api.del(`api/printers/${printer.id}`));
                refresh();
              }, { submitLabel: 'Supprimer' }),
            }),
          ]),
        ]));
      }
      table.append(tbody);
      list.append(el('div', { class: 'table-wrap' }, [table]));
    } catch (error) {
      toastError(error);
    }
  }

  function openEditor(printer) {
    const { body, read } = printerForm(printer || {});
    modal({
      title: printer ? `Modifier ${printer.name}` : 'Ajouter une imprimante',
      submitLabel: printer ? 'Enregistrer' : 'Ajouter',
      render: (container) => container.append(body),
      onSubmit: async () => {
        const payload = read();
        if (!payload.name) {
          toast('Le nom est obligatoire', 'warn');
          return false;
        }
        if (printer) await api.patch(`api/printers/${printer.id}`, payload);
        else await api.post('api/printers', payload);
        toast(printer ? 'Imprimante mise a jour' : 'Imprimante ajoutee', 'ok');
        refresh();
        return true;
      },
    });
  }

  function openDiscovery() {
    modal({
      title: 'Recherche sur le reseau local',
      submitLabel: null,
      wide: true,
      render: (body) => {
        const input = el('input', { placeholder: '192.168.1.0/24 (vide = sous-reseau detecte)' });
        const results = el('div', { style: 'margin-top:1rem' });
        const scanButton = el('button', {
          class: 'primary', text: 'Lancer le balayage',
          onClick: async () => {
            scanButton.disabled = true;
            clear(results).append(el('p', { class: 'muted', text: 'Balayage en cours, cela prend quelques secondes...' }));
            try {
              const network = input.value.trim();
              const data = await api.get(`api/printers/discover${network ? `?network=${encodeURIComponent(network)}` : ''}`);
              clear(results);
              results.append(el('p', { class: 'small muted', text: `Reseau balaye: ${data.network}` }));
              if (!data.results.length) {
                results.append(el('p', { text: 'Aucune imprimante detectee. Verifiez que le mode LAN est actif.' }));
                return;
              }
              for (const found of data.results) {
                results.append(el('div', { class: 'queue-item' }, [
                  el('div', { class: 'grow' }, [
                    el('div', { text: found.name || found.host }),
                    el('div', { class: 'small muted', text: `${found.host}:${found.port} — ${found.transport}${found.firmware ? ` — ${found.firmware}` : ''}` }),
                  ]),
                  found.known
                    ? el('span', { class: 'badge', text: 'deja ajoutee' })
                    : el('button', {
                        class: 'sm primary', text: 'Ajouter',
                        onClick: async () => {
                          const created = await run(() => api.post('api/printers', {
                            name: found.name || `Creality ${found.host}`,
                            model: found.model || 'K1',
                            transport: found.transport,
                            host: found.host,
                            port: found.port,
                          }), 'Imprimante ajoutee');
                          if (created) refresh();
                        },
                      }),
                ]));
              }
            } catch (error) {
              clear(results);
              toastError(error);
            } finally {
              scanButton.disabled = false;
            }
          },
        });
        body.append(
          el('p', { class: 'small muted', text: 'AllBudy teste les ports Moonraker (7125) et LAN Creality (9999) sur chaque adresse du sous-reseau. Aucune requete ne sort du reseau local.' }),
          el('div', { class: 'row' }, [el('div', { style: 'flex:1' }, [input]), scanButton]),
          results,
        );
      },
    });
  }

  refresh();
  return root;
}
