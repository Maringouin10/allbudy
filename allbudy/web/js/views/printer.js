/** Pilotage detaille d'une imprimante. */
import { api, apiUrl } from '../api.js';
import { cfsSection, fanPanel, printerIcon } from '../components.js';
import { on, printerState } from '../store.js';
import {
  badge, clear, confirmDialog, el, formatBytes, formatDuration, formatTemp, run, toastError,
} from '../ui.js';

const JOG_STEPS = [0.1, 1, 10, 50];

export function printerDetailView(printerId, navigate) {
  const id = Number(printerId);
  const header = el('div', { class: 'page-head' });
  const statusCard = el('div', { class: 'card' });
  const controlsCard = el('div', { class: 'card' });
  const cfsCard = el('div', { class: 'card' });
  const filesCard = el('div', { class: 'card' });
  const consoleCard = el('div', { class: 'card' });
  let printer = null;
  let jogStep = 10;
  /** Index de la bibliotheque par nom envoye a la machine (pieces, miniature). */
  let libraryByName = new Map();

  const root = el('div', {}, [
    header,
    el('div', { class: 'grid', style: 'grid-template-columns:repeat(auto-fit,minmax(340px,1fr))' }, [
      el('div', {}, [statusCard, cfsCard]),
      el('div', {}, [controlsCard, consoleCard]),
    ]),
    filesCard,
  ]);

  const command = async (action, body, successMessage) => {
    const result = await run(() => api.printerCommand(id, action, body), successMessage);
    return result;
  };

  function renderHeader() {
    const live = printerState(id);
    const state = live && live.connected ? live.status.state : 'offline';
    clear(header).append(
      el('button', { class: 'ghost', text: '← Parc', onClick: () => navigate('dashboard') }),
      printerIcon(printer ? printer.model : null),
      el('h1', { text: printer ? printer.name : `Imprimante #${id}` }),
      badge(state),
      el('div', { class: 'spacer' }),
      el('button', { text: 'Reconnecter', onClick: () => command('reconnect') }),
      el('button', {
        class: 'danger', text: 'Arret d\'urgence',
        onClick: () => confirmDialog(
          "L'arret d'urgence coupe immediatement moteurs et chauffes. L'imprimante devra etre redemarree. Continuer ?",
          () => command('emergency-stop'),
          { submitLabel: 'Arret d\'urgence' },
        ),
      }),
    );
  }

  function tempControl(label, heater, current, target) {
    const input = el('input', { type: 'number', min: 0, max: 350, value: Math.round(target || 0), style: 'width:5.5rem' });
    return el('div', { class: 'row', style: 'justify-content:space-between' }, [
      el('div', {}, [
        el('div', { class: 'small muted', text: label }),
        el('div', { style: 'font-size:1.1rem;font-weight:640', text: `${formatTemp(current, target)} °C` }),
      ]),
      el('div', { class: 'row' }, [
        input,
        el('button', {
          class: 'sm', text: 'Chauffer',
          onClick: () => command('temperature', { heater, value: Number(input.value) },
            `Consigne ${label} envoyee`),
        }),
        el('button', {
          class: 'sm ghost', text: 'Off',
          onClick: () => { input.value = 0; command('temperature', { heater, value: 0 }); },
        }),
      ]),
    ]);
  }

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

  function renderStatus() {
    const live = printerState(id);
    clear(statusCard);
    if (!live) {
      statusCard.append(el('p', { class: 'muted', text: 'Etat indisponible.' }));
      return;
    }
    const status = live.status || {};
    statusCard.append(el('h3', { text: 'Etat' }));

    if (!live.connected) {
      statusCard.append(el('p', { class: 'small', style: 'color:var(--err)', text: live.last_error || 'Imprimante hors ligne' }));
    }

    if (status.state === 'printing' || status.state === 'paused') {
      const extra = libraryEntry(status.filename);
      statusCard.append(
        extra.thumbnail
          ? el('img', { class: 'print-thumb', src: extra.thumbnail, alt: '', style: 'max-width:120px;border-radius:8px;float:right;margin:0 0 .4rem .6rem' })
          : null,
        el('div', { class: 'small truncate', text: status.filename || '' }),
        el('div', { class: 'progress-line', style: 'margin:.4rem 0' }, [
          el('div', { style: `width:${Math.min(100, status.progress || 0)}%` }),
        ]),
        el('div', { class: 'row small muted' }, [
          el('span', { text: `${Math.round(status.progress || 0)} %` }),
          status.current_layer ? el('span', { text: `couche ${status.current_layer}/${status.total_layers || '?'}` }) : null,
          status.elapsed_time != null ? el('span', { text: `ecoule ${formatDuration(status.elapsed_time)}` }) : null,
          status.remaining_time != null ? el('span', { text: `reste ${formatDuration(status.remaining_time)}` }) : null,
          extra.pieces ? el('span', { text: `${extra.pieces} piece(s)` }) : null,
        ]),
        el('div', { class: 'row', style: 'margin-top:.6rem' }, [
          status.state === 'printing'
            ? el('button', { text: 'Pause', onClick: () => command('pause') })
            : el('button', { class: 'primary', text: 'Reprendre', onClick: () => command('resume') }),
          el('button', {
            class: 'danger', text: 'Arreter',
            onClick: () => confirmDialog('Arreter l\'impression en cours ?', () => command('cancel'),
              { submitLabel: 'Arreter' }),
          }),
        ]),
      );
    }

    statusCard.append(
      el('div', { class: 'col', style: 'margin-top:.8rem' }, [
        tempControl('Buse', 'extruder', status.nozzle_temp, status.nozzle_target),
        tempControl('Plateau', 'bed', status.bed_temp, status.bed_target),
        status.chamber_temp != null
          ? tempControl('Chambre', 'chamber', status.chamber_temp, status.chamber_target)
          : null,
      ]),
    );

    const details = [];
    if (status.position && status.position.z != null) {
      details.push(`X ${status.position.x} · Y ${status.position.y} · Z ${status.position.z}`);
    }
    if (status.speed_factor) details.push(`vitesse ${Math.round(status.speed_factor)} %`);
    if (status.firmware) details.push(status.firmware);
    if (details.length) {
      statusCard.append(el('div', { class: 'small muted mono', style: 'margin-top:.6rem', text: details.join('  ·  ') }));
    }

    if (printer && printer.camera_url) {
      statusCard.append(el('img', {
        src: printer.camera_url,
        alt: 'Flux camera',
        style: 'width:100%;border-radius:8px;margin-top:.8rem;border:1px solid var(--line)',
        onerror: (event) => { event.target.style.display = 'none'; },
      }));
    }
  }

  function renderControls() {
    const live = printerState(id);
    const status = (live && live.status) || {};
    clear(controlsCard).append(el('h3', { text: 'Commandes' }));

    const stepRow = el('div', { class: 'row', style: 'margin-bottom:.5rem' }, [
      el('span', { class: 'small muted', text: 'Pas' }),
      ...JOG_STEPS.map((step) => el('button', {
        class: `sm ${step === jogStep ? 'primary' : ''}`,
        text: `${step} mm`,
        onClick: () => { jogStep = step; renderControls(); },
      })),
    ]);

    const jogButton = (label, axis, sign) => el('button', {
      text: label,
      onClick: () => command('move', { axis, distance: sign * jogStep, speed: axis === 'Z' ? 900 : 3000 }),
    });

    const jog = el('div', { class: 'jog' }, [
      el('div', { class: 'empty' }), jogButton('Y+', 'Y', 1), el('div', { class: 'empty' }),
      jogButton('X-', 'X', -1),
      el('button', { text: '⌂', title: 'Prise d\'origine XYZ', onClick: () => command('home', { axes: 'XYZ' }) }),
      jogButton('X+', 'X', 1),
      el('div', { class: 'empty' }), jogButton('Y-', 'Y', -1), el('div', { class: 'empty' }),
      el('div', { class: 'empty' }), jogButton('Z+', 'Z', 1), el('div', { class: 'empty' }),
      el('div', { class: 'empty' }), jogButton('Z-', 'Z', -1), el('div', { class: 'empty' }),
    ]);

    const extrudeAmount = el('input', { type: 'number', value: 10, style: 'width:5rem' });
    const speedInput = el('input', { type: 'number', value: Math.round(status.speed_factor || 100), style: 'width:5rem' });

    controlsCard.append(
      stepRow,
      jog,
      el('div', { class: 'row', style: 'margin-top:.8rem' }, [
        el('span', { class: 'small muted', text: 'Extrudeur' }),
        extrudeAmount,
        el('button', { class: 'sm', text: 'Extruder', onClick: () => command('extrude', { distance: Number(extrudeAmount.value) }) }),
        el('button', { class: 'sm', text: 'Retracter', onClick: () => command('extrude', { distance: -Number(extrudeAmount.value) }) }),
      ]),
      el('div', { class: 'row', style: 'margin-top:.5rem' }, [
        el('span', { class: 'small muted', text: 'Vitesse %' }),
        speedInput,
        el('button', { class: 'sm', text: 'Appliquer', onClick: () => command('speed', { percent: Number(speedInput.value) }) }),
      ]),
      el('div', { class: 'card-section', style: 'margin-top:.7rem' }, [
        el('div', { class: 'section-title', text: 'Ventilateurs' }),
        fanPanel(status.fans, { printerId: id, onChange: renderControls })
          || el('div', { class: 'small muted', text: 'Aucun ventilateur remonte par cette machine.' }),
      ]),
      el('div', { class: 'row', style: 'margin-top:.5rem' }, [
        el('button', { class: 'sm', text: 'Lumiere ON', onClick: () => command('light', { on: true }) }),
        el('button', { class: 'sm', text: 'Lumiere OFF', onClick: () => command('light', { on: false }) }),
        el('button', { class: 'sm', text: 'Moteurs OFF', onClick: () => command('gcode', { script: 'M84' }) }),
      ]),
    );
  }

  function renderCfs() {
    const live = printerState(id);
    const spools = (live && live.status && live.status.spools) || [];
    const capabilities = (live && live.capabilities) || {};
    const connected = !!(live && live.connected);
    clear(cfsCard).append(
      el('h3', { text: 'Filaments (CFS)' }),
      cfsSection(spools, capabilities, { connected }),
    );
    if (!spools.length) {
      cfsCard.append(el('p', { class: 'small muted', style: 'margin-top:.5rem', text: 'Declarez les bobines montees depuis l\'ecran Filaments pour que la file d\'attente sache quoi envoyer ici, meme sans CFS detecte automatiquement.' }));
      cfsCard.append(el('button', { class: 'sm', text: 'Ouvrir Filaments', onClick: () => navigate('spools') }));
    } else {
      cfsCard.append(el('div', { class: 'small muted', style: 'margin-top:.5rem', text: 'Etat lu directement sur la machine et synchronise avec l\'inventaire.' }));
    }
  }

  async function renderFiles() {
    clear(filesCard).append(el('div', { class: 'row' }, [
      el('h3', { text: 'Fichiers sur la machine' }),
      el('div', { style: 'flex:1' }),
      el('button', { class: 'sm', text: 'Rafraichir', onClick: renderFiles }),
    ]));
    try {
      const files = await api.get(`api/printers/${id}/files`);
      if (!files.length) {
        filesCard.append(el('p', { class: 'small muted', text: 'Aucun fichier sur cette imprimante.' }));
        return;
      }
      const tbody = el('tbody');
      for (const file of files.slice(0, 200)) {
        tbody.append(el('tr', {}, [
          el('td', { class: 'truncate', text: file.name }),
          el('td', { class: 'small muted', text: formatBytes(file.size) }),
          el('td', { class: 'actions' }, [
            el('button', {
              class: 'sm danger', text: 'Supprimer',
              onClick: () => confirmDialog(`Supprimer ${file.name} de l'imprimante ?`, async () => {
                await run(() => api.del(`api/printers/${id}/files/${encodeURIComponent(file.name)}`));
                renderFiles();
              }, { submitLabel: 'Supprimer' }),
            }),
          ]),
        ]));
      }
      filesCard.append(el('div', { class: 'table-wrap' }, [
        el('table', {}, [
          el('thead', {}, [el('tr', {}, [el('th', { text: 'Nom' }), el('th', { text: 'Taille' }), el('th', {})])]),
          tbody,
        ]),
      ]));
    } catch (error) {
      filesCard.append(el('p', { class: 'small muted', text: `Liste indisponible: ${error.message}` }));
    }
  }

  function renderConsole() {
    const output = el('div', { class: 'console' });
    const input = el('input', { placeholder: 'G28, M105, SET_PIN PIN=...', class: 'mono' });
    const send = async () => {
      const script = input.value.trim();
      if (!script) return;
      output.append(el('div', { class: 'line sent', text: `> ${script}` }));
      input.value = '';
      try {
        await api.printerCommand(id, 'gcode', { script });
        output.append(el('div', { class: 'line muted', text: 'envoye' }));
      } catch (error) {
        output.append(el('div', { class: 'line err', text: error.message }));
      }
      output.scrollTop = output.scrollHeight;
    };
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') send(); });

    clear(consoleCard).append(
      el('h3', { text: 'Console G-code' }),
      output,
      el('div', { class: 'row', style: 'margin-top:.5rem' }, [
        el('div', { style: 'flex:1' }, [input]),
        el('button', { class: 'sm primary', text: 'Envoyer', onClick: send }),
      ]),
      el('div', { class: 'small muted', style: 'margin-top:.4rem', text: 'Les reponses de la machine ne sont pas remontees par tous les firmwares: seul l\'envoi est confirme.' }),
    );
  }

  async function loadLibrary() {
    try {
      const data = await api.files('?limit=500');
      libraryByName = new Map(data.items.map((file) => [file.stored_name, file]));
      renderStatus();
    } catch (error) {
      // Sans la bibliotheque, le statut reste affichable: on n'alerte pas.
      console.warn('Index de la bibliotheque indisponible', error);
    }
  }

  async function load() {
    try {
      printer = await api.get(`api/printers/${id}`);
    } catch (error) {
      toastError(error);
      navigate('printers');
      return;
    }
    renderHeader();
    renderStatus();
    renderControls();
    renderCfs();
    renderConsole();
    renderFiles();
    loadLibrary();
  }

  const unsubscribe = on('printer', (entry) => {
    if (entry.printer_id !== id) return;
    renderHeader();
    renderStatus();
    renderCfs();
  });
  root.cleanup = unsubscribe;

  load();
  return root;
}
