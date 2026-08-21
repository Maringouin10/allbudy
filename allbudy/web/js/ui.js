/** Helpers de rendu: creation d'elements, formatage, toasts, modales. */

/** Cree un element DOM. `attrs.html` injecte du HTML, `attrs.text` du texte. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'value') node.value = value;
    else if (key === 'checked' || key === 'disabled' || key === 'selected') node[key] = !!value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* --------------------------------------------------------------- formatage */

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const total = Math.max(0, Math.round(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days} j ${hours} h`;
  if (hours) return `${hours} h ${String(minutes).padStart(2, '0')}`;
  if (minutes) return `${minutes} min`;
  return `${total} s`;
}

export function formatBytes(bytes) {
  if (!bytes) return '0 o';
  const units = ['o', 'Ko', 'Mo', 'Go', 'To'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${value >= 100 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

export function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function formatTemp(current, target) {
  const now = Number(current || 0).toFixed(0);
  if (target && target > 0) return `${now} / ${Number(target).toFixed(0)}`;
  return `${now}`;
}

const STATE_LABELS = {
  offline: 'Hors ligne',
  idle: 'Prete',
  printing: 'Impression',
  paused: 'En pause',
  complete: 'Terminee',
  error: 'Erreur',
  busy: 'Occupee',
};

const JOB_LABELS = {
  queued: 'En file',
  assigned: 'Attribue',
  sending: 'Envoi',
  printing: 'Impression',
  paused: 'En pause',
  completed: 'Termine',
  failed: 'Echec',
  cancelled: 'Annule',
};

export const stateLabel = (state) => STATE_LABELS[state] || state || '—';
export const jobLabel = (status) => JOB_LABELS[status] || status || '—';

export function badge(state, labels = STATE_LABELS) {
  return el('span', { class: `badge ${state}`, text: labels[state] || state });
}

export const jobBadge = (status) => badge(status, JOB_LABELS);

/** Noir ou blanc selon la luminance, pour ecrire sur une pastille de couleur. */
export function contrastColor(hex) {
  const value = String(hex || '').replace('#', '');
  if (value.length !== 6) return '#fff';
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#111' : '#fff';
}

/* ------------------------------------------------------------------ toasts */

export function toast(message, kind = 'info', timeout = 4200) {
  const container = document.getElementById('toasts');
  const node = el('div', { class: `toast ${kind}`, text: message });
  container.append(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .25s';
    setTimeout(() => node.remove(), 260);
  }, timeout);
  return node;
}

export const toastError = (error) =>
  toast(error && error.message ? error.message : String(error), 'err', 6000);

/** Enveloppe une action asynchrone: affiche le resultat ou l'erreur. */
export async function run(promiseOrFn, successMessage) {
  try {
    const result = await (typeof promiseOrFn === 'function' ? promiseOrFn() : promiseOrFn);
    const message = successMessage || (result && result.message);
    if (message) toast(message, 'ok');
    return result;
  } catch (error) {
    toastError(error);
    return null;
  }
}

/* ----------------------------------------------------------------- modales */

/**
 * Ouvre une modale. `render(body, close)` remplit le corps.
 * `onSubmit` recoit la modale et doit retourner true pour fermer.
 */
export function modal({ title, render, submitLabel = 'Valider', onSubmit, wide = false }) {
  const body = el('div', { class: 'body' });
  const backdrop = el('div', { class: 'modal-backdrop' });
  const close = () => backdrop.remove();

  const submitButton = onSubmit
    ? el('button', { class: 'primary', onClick: async () => {
        submitButton.disabled = true;
        try {
          const keepOpen = await onSubmit(body);
          if (keepOpen !== false) close();
        } catch (error) {
          toastError(error);
        } finally {
          submitButton.disabled = false;
        }
      }, text: submitLabel })
    : null;

  const box = el('div', { class: 'modal', style: wide ? 'width:min(820px,100%)' : null }, [
    el('header', {}, [el('h2', { text: title })]),
    body,
    el('footer', {}, [
      el('button', { class: 'ghost', text: onSubmit ? 'Annuler' : 'Fermer', onClick: close }),
      submitButton,
    ]),
  ]);

  backdrop.append(box);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener('keydown', function onKey(event) {
    if (event.key === 'Escape') {
      close();
      document.removeEventListener('keydown', onKey);
    }
  });
  document.body.append(backdrop);
  render(body, close);
  const firstInput = body.querySelector('input, select, textarea');
  if (firstInput) firstInput.focus();
  return { body, close };
}

export function confirmDialog(message, onConfirm, { submitLabel = 'Confirmer' } = {}) {
  return modal({
    title: 'Confirmation',
    submitLabel,
    render: (body) => body.append(el('p', { text: message })),
    onSubmit: onConfirm,
  });
}

/** Champ de formulaire etiquete. */
export function field(label, input, hint) {
  return el('div', { class: 'field' }, [
    el('label', { text: label }),
    input,
    hint ? el('div', { class: 'small muted', text: hint, style: 'margin-top:.25rem' }) : null,
  ]);
}

export function emptyState(icon, message, action) {
  return el('div', { class: 'empty-state' }, [
    el('div', { class: 'big', text: icon }),
    el('div', { text: message }),
    action ? el('div', { style: 'margin-top:1rem' }, [action]) : null,
  ]);
}
