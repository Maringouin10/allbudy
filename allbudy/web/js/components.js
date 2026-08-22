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

/**
 * Bloc CFS complet: emplacements s'il y en a, sinon un etat explicite plutot
 * que de disparaitre silencieusement. `capabilities` vient de la charge
 * utile temps reel (`entry.capabilities`) et reflete la detection en direct,
 * independamment de ce qui est enregistre en base.
 */
export function cfsSection(spools = [], capabilities = {}, { connected = true } = {}) {
  const panel = cfsPanel(spools);
  if (panel) {
    return el('div', { class: 'card-section' }, [
      el('div', { class: 'section-title' }, [
        'CFS',
        el('span', { class: 'small muted', text: ` ${spools.length} emplacement(s)` }),
      ]),
      panel,
    ]);
  }

  // Hors ligne, l'absence de CFS n'est pas confirmee: on ne l'affirme pas.
  let message = 'Aucun CFS detecte sur cette imprimante.';
  let warn = false;
  if (!connected) {
    message = 'Imprimante hors ligne : etat du CFS inconnu.';
  } else if (capabilities && capabilities.cfs) {
    message = 'Objet CFS detecte sur la machine mais aucun emplacement reconnu. Voir les journaux du serveur (docker compose logs).';
    warn = true;
  }
  return el('div', { class: 'card-section' }, [
    el('div', { class: 'section-title', text: 'CFS' }),
    el('div', { class: `cfs-empty${warn ? ' warn' : ''}`, text: message }),
  ]);
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

/* ----------------------------------------------------------- icone modele */

/**
 * Silhouettes abstraites par famille de machine (pas de photo produit): une
 * forme distincte suffit a reconnaitre le type au premier coup d'oeil dans
 * une liste, a la maniere des icones d'imprimante de Bambuddy.
 */
const ICON_GANTRY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v14"/><path d="M4 20h16"/><path d="M9 7v3"/><circle cx="9" cy="11" r="1.4" fill="currentColor" stroke="none"/><path d="M7 17h10"/></svg>';
const ICON_ENCLOSED = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 15h16"/><circle cx="17" cy="17.2" r=".9" fill="currentColor" stroke="none"/></svg>';
const ICON_BEDSLINGER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 20V6"/><path d="M18 20V6"/><path d="M6 6h12"/><rect x="3" y="18" width="8" height="3" rx="1"/><circle cx="12" cy="9" r="1.2" fill="currentColor" stroke="none"/></svg>';
const ICON_GENERIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="14" width="16" height="6" rx="1"/><path d="M8 14V8h8v6"/><path d="M12 4v4"/></svg>';

const MODEL_ICON_FAMILIES = [
  [/k1\s*max/i, ICON_GANTRY],
  [/k1c?\b/i, ICON_GANTRY],
  [/k2/i, ICON_ENCLOSED],
  [/ender/i, ICON_BEDSLINGER],
];

function familyIcon(model) {
  const text = String(model || '');
  for (const [test, svg] of MODEL_ICON_FAMILIES) {
    if (test.test(text)) return svg;
  }
  return ICON_GENERIC;
}

//: Vraies photos produit pour les familles les plus courantes du parc; les
//: autres modeles restent sur la silhouette generique ci-dessus.
const MODEL_PHOTOS = [
  [/k2/i, 'https://cdn.shopify.com/s/files/1/0911/3233/0284/files/imgi_1_K2Combo-_4.png?v=1769932494'],
  [/ender-?\s*3\s*v3/i, 'https://cdn.tanguay.ca/images/products/1920px/0856639.jpg'],
];

function photoFor(model) {
  const text = String(model || '');
  for (const [test, url] of MODEL_PHOTOS) {
    if (test.test(text)) return url;
  }
  return null;
}

/** Petite icone de modele, comme sur les cartes d'imprimante de Bambuddy. */
export function printerIcon(model, { size = 32 } = {}) {
  const photo = photoFor(model);
  const wrap = el('div', {
    class: `printer-icon${photo ? ' photo' : ''}`,
    style: `width:${size}px;height:${size}px`,
    title: model || 'Modele inconnu',
    html: photo ? null : familyIcon(model),
  });
  if (photo) {
    wrap.append(el('img', {
      src: photo,
      alt: '',
      loading: 'lazy',
      // L'image est hebergee ailleurs: si elle devient inaccessible, on
      // retombe sur la silhouette generique plutot que sur un cadre vide.
      onerror: () => {
        wrap.classList.remove('photo');
        wrap.innerHTML = ICON_GENERIC;
      },
    }));
  }
  return wrap;
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
    printerIcon(status.model),
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

  body.push(cfsSection(status.spools, entry.capabilities, { connected }));

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
