/** Composants partages entre plusieurs vues. */
import { api } from './api.js';
import {
  badge, confirmDialog, contrastColor, el, formatDuration, formatTemp, run, stateLabel,
} from './ui.js';

/** Pastilles des emplacements CFS. */
export function slotChips(spools = []) {
  if (!spools.length) return null;
  return el(
    'div',
    { class: 'slots' },
    spools.map((spool) => {
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
    }),
  );
}

/** Cellule de telemetrie (temperature, couche, temps). */
function cell(key, value, tail) {
  return el('div', { class: 'cell' }, [
    el('div', { class: 'k', text: key }),
    el('div', { class: 'v', text: value }),
    tail ? el('div', { class: 't', text: tail }) : null,
  ]);
}

/**
 * Carte d'imprimante du tableau de bord.
 * `entry` est la charge utile temps reel du WebSocket.
 */
export function printerCard(entry, { onOpen, onRefresh } = {}) {
  const status = entry.status || {};
  const connected = entry.connected;
  const state = connected ? status.state : 'offline';
  const printing = state === 'printing' || state === 'paused';

  const command = async (action, body) => {
    await run(() => api.printerCommand(entry.printer_id, action, body));
    if (onRefresh) onRefresh();
  };

  const header = el('header', {}, [
    el('div', { style: 'min-width:0' }, [
      el('div', { class: 'name truncate', text: entry.name }),
      el('div', { class: 'sub truncate', text: status.model || entry.transport }),
    ]),
    el('div', { class: 'spacer' }),
    badge(state),
  ]);

  const body = [];
  if (!connected) {
    body.push(el('div', { class: 'small muted', text: entry.last_error || 'Non connectee' }));
  }

  if (printing) {
    body.push(el('div', { class: 'small truncate', text: status.filename || 'Impression en cours' }));
    body.push(el('div', { class: 'progress-line' }, [
      el('div', { style: `width:${Math.min(100, status.progress || 0)}%` }),
    ]));
    body.push(el('div', { class: 'row small muted' }, [
      el('span', { text: `${Math.round(status.progress || 0)} %` }),
      status.current_layer
        ? el('span', { text: `couche ${status.current_layer}${status.total_layers ? ` / ${status.total_layers}` : ''}` })
        : null,
      el('span', { class: 'spacer', style: 'flex:1' }),
      status.remaining_time != null
        ? el('span', { text: `reste ${formatDuration(status.remaining_time)}` })
        : null,
    ]));
  }

  body.push(el('div', { class: 'telemetry' }, [
    cell('Buse', formatTemp(status.nozzle_temp, status.nozzle_target), '°C'),
    cell('Plateau', formatTemp(status.bed_temp, status.bed_target), '°C'),
    status.chamber_temp != null ? cell('Chambre', `${Math.round(status.chamber_temp)}`, '°C') : null,
    status.part_fan != null ? cell('Ventilo', `${Math.round(status.part_fan)}`, '%') : null,
  ].filter(Boolean)));

  const chips = slotChips(status.spools);
  if (chips) body.push(chips);

  const actions = el('div', { class: 'row' }, [
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
  ]);

  return el('div', { class: 'card printer-card' }, [header, ...body, actions]);
}

/** Ligne de statistique du tableau de bord. */
export function statCard(label, value, hint) {
  return el('div', { class: 'card' }, [
    el('div', { class: 'small muted', text: label }),
    el('div', { style: 'font-size:1.6rem;font-weight:650;line-height:1.2', text: String(value) }),
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
