/** Composants partages entre plusieurs vues. */
import { api } from './api.js';
import {
  badge, confirmDialog, contrastColor, el, formatDuration, formatTemp, run, stateLabel,
} from './ui.js';

/* ------------------------------------------------------------------- CFS */

/**
 * Emplacements du CFS, rendus comme de petites bobines.
 * L'etat vient du WebSocket, donc il suit la machine en direct.
 */
export function cfsPanel(spools = [], { compact = false } = {}) {
  if (!spools.length) return null;
  const slots = spools.map((spool) => {
    const percent = spool.remaining_pct != null ? Math.max(0, Math.min(100, spool.remaining_pct)) : null;
    const color = spool.color_hex || '#7f8c8d';
    const classes = ['cfs-slot'];
    if (spool.active) classes.push('active');
    if (spool.empty) classes.push('empty');

    return el('div', {
      class: classes.join(' '),
      title: `Unite ${spool.unit} · emplacement ${spool.slot}`
        + (spool.vendor ? ` · ${spool.vendor}` : '')
        + (percent != null ? ` · ${Math.round(percent)} % restant` : ''),
    }, [
      el('div', { class: 'cfs-spool', style: `--spool-color:${color}` }, [
        // La hauteur du remplissage represente le filament restant.
        el('div', {
          class: 'cfs-fill',
          style: `height:${percent == null ? 100 : percent}%;background:${color}`,
        }),
        spool.active ? el('span', { class: 'cfs-active-dot', title: 'Charge jusqu\'a la buse' }) : null,
      ]),
      el('div', { class: 'cfs-label' }, [
        el('div', { class: 'cfs-mat truncate', text: spool.empty ? 'vide' : (spool.material || '?') }),
        el('div', { class: 'cfs-pct', text: percent != null ? `${Math.round(percent)} %` : `#${spool.slot}` }),
      ]),
    ]);
  });

  return el('div', { class: `cfs-panel${compact ? ' compact' : ''}` }, slots);
}

/** Ancienne representation compacte, encore utilisee dans les listes denses. */
export function slotChips(spools = []) {
  if (!spools.length) return null;
  return el('div', { class: 'slots' }, spools.map((spool) => {
    const classes = ['slot'];
    if (spool.active) classes.push('active');
    if (spool.empty) classes.push('empty');
    const label = spool.empty ? 'vide' : (spool.material || '?');
    const remaining = spool.remaining_pct != null ? ` ${Math.round(spool.remaining_pct)}%` : '';
    return el('span', {
      class: classes.join(' '),
      title: `Unite ${spool.unit} / emplacement ${spool.slot} — ${label}${remaining}`,
    }, [
      el('span', { class: 'chip', style: `background:${spool.color_hex || '#7f8c8d'}` }),
      `${label}${remaining}`,
    ]);
  }));
}

/* -------------------------------------------------------------- ventilos */

/**
 * Panneau ventilateurs: vitesse, regime, et pilotage direct quand la machine
 * l'autorise. Les ventilateurs asservis au firmware sont affichés en lecture seule.
 */
export function fanPanel(fans = [], { printerId, onChange } = {}) {
  if (!fans.length) return null;

  const rows = fans.map((fan) => {
    const speed = fan.speed == null ? 0 : Math.round(fan.speed);
    const gauge = el('div', { class: 'fan-gauge' }, [
      el('div', { style: `width:${Math.max(0, Math.min(100, speed))}%` }),
    ]);

    const setSpeed = async (value) => {
      await run(() => api.printerCommand(printerId, 'fan', { fan: fan.key, speed: value }));
      if (onChange) onChange();
    };

    return el('div', { class: 'fan-row' }, [
      el('div', { class: 'fan-head' }, [
        el('span', {
          class: `fan-icon${speed > 0 ? ' spinning' : ''}`,
          text: '✱',
          title: speed > 0 ? 'En rotation' : 'A l\'arret',
        }),
        el('span', { class: 'fan-name truncate', text: fan.label }),
        el('span', { class: 'fan-value', text: `${speed} %` }),
        fan.rpm != null
          ? el('span', { class: 'fan-rpm', text: `${fan.rpm} tr/min` })
          : null,
      ]),
      gauge,
      fan.controllable && printerId
        ? el('div', { class: 'fan-actions' }, [0, 50, 100].map((preset) => el('button', {
            class: `sm ${speed === preset ? 'primary' : 'ghost'}`,
            text: preset === 0 ? 'Off' : `${preset} %`,
            onClick: () => setSpeed(preset),
          })))
        : el('span', { class: 'fan-readonly', text: 'piloté par la machine' }),
    ]);
  });

  return el('div', { class: 'fan-panel' }, rows);
}

/* ----------------------------------------------------------------- stats */

function statLine(label, value, accent) {
  return el('div', { class: 'stat-line' }, [
    el('span', { class: 'k', text: label }),
    el('span', { class: `v${accent ? ' accent' : ''}`, text: value }),
  ]);
}

/** Heure de fin estimee, calculee depuis le temps restant. */
function estimatedEnd(remainingSeconds) {
  if (remainingSeconds == null) return null;
  const end = new Date(Date.now() + remainingSeconds * 1000);
  const today = new Date();
  const sameDay = end.toDateString() === today.toDateString();
  const time = end.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  return sameDay ? time : `${end.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })} ${time}`;
}

/**
 * Encart detaille de l'impression en cours.
 * `pieces` est le nombre de pieces sur le plateau, lu dans les metadonnees.
 */
export function printStatsBox(status, { pieces = null, thumbnail = null } = {}) {
  const progress = Math.max(0, Math.min(100, status.progress || 0));
  const layers = status.total_layers
    ? `${status.current_layer || 0} / ${status.total_layers}`
    : (status.current_layer != null ? String(status.current_layer) : '—');

  const lines = [
    statLine('Couche', layers),
    statLine('Ecoule', formatDuration(status.elapsed_time)),
    statLine('Restant', formatDuration(status.remaining_time), true),
    statLine('Fin prevue', estimatedEnd(status.remaining_time) || '—'),
  ];
  if (pieces) lines.push(statLine('Pieces', `${pieces}`));
  if (status.speed_factor) lines.push(statLine('Vitesse', `${Math.round(status.speed_factor)} %`));
  if (status.flow_factor) lines.push(statLine('Debit', `${Math.round(status.flow_factor)} %`));
  if (status.position && status.position.z != null) {
    lines.push(statLine('Hauteur Z', `${status.position.z} mm`));
  }

  return el('div', { class: 'print-box' }, [
    el('div', { class: 'print-head' }, [
      thumbnail
        ? el('img', { class: 'print-thumb', src: thumbnail, alt: '', loading: 'lazy' })
        : el('div', { class: 'print-thumb ph', text: '🧩' }),
      el('div', { class: 'print-title' }, [
        el('div', { class: 'truncate', title: status.filename || '', text: status.filename || 'Impression' }),
        el('div', { class: 'print-progress-row' }, [
          el('div', { class: 'progress-line' }, [el('div', { style: `width:${progress}%` })]),
          el('span', { class: 'print-pct', text: `${progress.toFixed(1)} %` }),
        ]),
      ]),
    ]),
    el('div', { class: 'print-stats' }, lines),
  ]);
}

/* ------------------------------------------------------------ telemetrie */

function cell(key, value, tail) {
  return el('div', { class: 'cell' }, [
    el('div', { class: 'k', text: key }),
    el('div', { class: 'v', text: value }),
    tail ? el('div', { class: 't', text: tail }) : null,
  ]);
}

export function temperatureCells(status) {
  return el('div', { class: 'telemetry' }, [
    cell('Buse', formatTemp(status.nozzle_temp, status.nozzle_target), '°C'),
    cell('Plateau', formatTemp(status.bed_temp, status.bed_target), '°C'),
    status.chamber_temp != null
      ? cell('Chambre', formatTemp(status.chamber_temp, status.chamber_target), '°C')
      : null,
  ].filter(Boolean));
}

/* ------------------------------------------------------- carte imprimante */

/**
 * Carte d'imprimante du tableau de bord.
 * `entry` est la charge utile temps reel poussee par le WebSocket.
 */
export function printerCard(entry, { onOpen, onRefresh, pieces = null, thumbnail = null } = {}) {
  const status = entry.status || {};
  const connected = entry.connected;
  const state = connected ? status.state : 'offline';
  const printing = state === 'printing' || state === 'paused';

  const command = async (action, body) => {
    await run(() => api.printerCommand(entry.printer_id, action, body));
    if (onRefresh) onRefresh();
  };

  // Bandeau de connexion: l'etat du lien est distinct de l'etat d'impression.
  const link = el('div', { class: `link-state ${connected ? 'up' : 'down'}` }, [
    el('span', { class: 'led' }),
    el('span', { text: connected ? 'Connectée' : 'Hors ligne' }),
    el('span', { class: 'link-sep', text: '·' }),
    el('span', { class: 'small muted truncate', text: entry.transport }),
  ]);

  const header = el('header', {}, [
    el('div', { style: 'min-width:0' }, [
      el('div', { class: 'name truncate', text: entry.name }),
      el('div', { class: 'sub truncate', text: status.model || '—' }),
    ]),
    el('div', { class: 'spacer' }),
    badge(state),
  ]);

  const body = [header, link];

  if (!connected) {
    body.push(el('div', { class: 'card-error small', text: entry.last_error || 'Aucune connexion établie.' }));
  }

  if (printing) {
    body.push(printStatsBox(status, { pieces, thumbnail }));
  }

  body.push(temperatureCells(status));

  const fans = fanPanel(status.fans, {
    printerId: entry.printer_id,
    onChange: onRefresh,
  });
  if (fans) {
    body.push(el('div', { class: 'card-section' }, [
      el('div', { class: 'section-title', text: 'Ventilateurs' }),
      fans,
    ]));
  }

  const cfs = cfsPanel(status.spools);
  if (cfs) {
    body.push(el('div', { class: 'card-section' }, [
      el('div', { class: 'section-title' }, [
        'CFS',
        el('span', { class: 'small muted', text: ` ${status.spools.length} emplacements` }),
      ]),
      cfs,
    ]));
  }

  body.push(el('div', { class: 'row card-actions' }, [
    el('button', { class: 'sm', text: 'Details', onClick: () => onOpen && onOpen(entry.printer_id) }),
    el('div', { style: 'flex:1' }),
    state === 'printing'
      ? el('button', { class: 'sm', text: 'Pause', onClick: () => command('pause') })
      : null,
    state === 'paused'
      ? el('button', { class: 'sm primary', text: 'Reprendre', onClick: () => command('resume') })
      : null,
    printing
      ? el('button', {
          class: 'sm danger',
          text: 'Arreter',
          onClick: () => confirmDialog(
            `Arreter l'impression sur ${entry.name} ? Le travail en cours sera perdu.`,
            () => command('cancel'),
            { submitLabel: 'Arreter' },
          ),
        })
      : null,
    !connected
      ? el('button', { class: 'sm', text: 'Reconnecter', onClick: () => command('reconnect') })
      : null,
  ]));

  return el('div', { class: `card printer-card${connected ? '' : ' offline'}` }, body);
}

/** Tuile de statistique du tableau de bord. */
export function statCard(label, value, hint) {
  return el('div', { class: 'card stat-card' }, [
    el('div', { class: 'small muted', text: label }),
    el('div', { class: 'stat-value', text: String(value) }),
    hint ? el('div', { class: 'small muted', text: hint }) : null,
  ]);
}

/** Pastille de couleur avec libelle materiau. */
export function materialChip(material, color) {
  return el('span', {
    class: 'badge',
    style: `background:${color};color:${contrastColor(color)};border-color:transparent`,
    text: material || '—',
  });
}

export { stateLabel };
