/** Webhooks sortants: fin d'impression, plateau refroidi (embarque dans Reglages). */
import { api } from '../api.js';
import {
  clear, confirmDialog, el, emptyState, field, formatDate, modal, run, toast, toastError,
} from '../ui.js';

const EVENTS = [
  ['print_finished', 'Fin d\'impression'],
  ['bed_cold', 'Plateau refroidi'],
];

function webhookForm(webhook = {}, printers = []) {
  const scopeInitial = webhook.printer_id ? 'printer' : webhook.tag ? 'tag' : 'all';
  const fields = {
    name: el('input', { value: webhook.name || '', placeholder: 'Discord atelier' }),
    event: el('select', {}, EVENTS.map(([value, label]) =>
      el('option', { value, text: label, selected: (webhook.event || 'print_finished') === value }))),
    url: el('input', { value: webhook.url || '', placeholder: 'https://...' }),
    scope: el('select', {}, [
      el('option', { value: 'all', text: 'Toutes les imprimantes', selected: scopeInitial === 'all' }),
      el('option', { value: 'tag', text: 'Un groupe (etiquette)', selected: scopeInitial === 'tag' }),
      el('option', { value: 'printer', text: 'Une imprimante precise', selected: scopeInitial === 'printer' }),
    ]),
    tag: el('input', { value: webhook.tag || '', placeholder: 'atelier, chambre-chauffee...' }),
    printer_id: el('select', {}, printers.map((p) => el('option', {
      value: p.id, text: p.name, selected: webhook.printer_id === p.id,
    }))),
    bed_cold_threshold: el('input', { type: 'number', min: 0, max: 120, value: webhook.bed_cold_threshold ?? 40 }),
    enabled: el('input', { type: 'checkbox', checked: webhook.enabled !== false }),
  };

  const tagRow = field('Etiquette', fields.tag, 'Toutes les imprimantes portant cette etiquette');
  const printerRow = field('Imprimante', fields.printer_id);
  const thresholdRow = field('Seuil « plateau froid » (°C)', fields.bed_cold_threshold, 'Le webhook part quand le plateau redescend sous cette temperature');

  const syncVisibility = () => {
    tagRow.style.display = fields.scope.value === 'tag' ? '' : 'none';
    printerRow.style.display = fields.scope.value === 'printer' ? '' : 'none';
    thresholdRow.style.display = fields.event.value === 'bed_cold' ? '' : 'none';
  };
  fields.scope.addEventListener('change', syncVisibility);
  fields.event.addEventListener('change', syncVisibility);

  const body = el('div', {}, [
    field('Nom', fields.name),
    el('div', { class: 'field-row' }, [
      field('Evenement', fields.event),
      field('Portee', fields.scope),
    ]),
    tagRow,
    printerRow,
    thresholdRow,
    field('URL', fields.url, 'Recoit un POST JSON a chaque declenchement'),
    el('div', { class: 'check' }, [fields.enabled, el('label', { text: 'Webhook actif' })]),
  ]);
  syncVisibility();

  const read = () => {
    const payload = {
      name: fields.name.value.trim(),
      event: fields.event.value,
      url: fields.url.value.trim(),
      bed_cold_threshold: Number(fields.bed_cold_threshold.value) || 40,
      enabled: fields.enabled.checked,
      tag: fields.scope.value === 'tag' ? (fields.tag.value.trim() || null) : null,
      printer_id: fields.scope.value === 'printer' && fields.printer_id.value
        ? Number(fields.printer_id.value) : null,
    };
    return payload;
  };

  return { body, read };
}

function scopeLabel(webhook, printers) {
  if (webhook.printer_id) {
    const printer = printers.find((p) => p.id === webhook.printer_id);
    return printer ? printer.name : `imprimante #${webhook.printer_id}`;
  }
  if (webhook.tag) return `groupe « ${webhook.tag} »`;
  return 'toutes les imprimantes';
}

/** Section "Webhooks" embarquee dans Reglages. */
export function webhooksSettingsSection() {
  const list = el('div', { class: 'col' });
  let printers = [];
  const root = el('div', {}, [
    el('div', { class: 'row', style: 'margin-bottom:.5rem' }, [
      el('p', { class: 'small muted', style: 'flex:1;margin:0', text: 'Notifiez un service externe (Discord, Home Assistant, n8n...) a la fin d\'une impression ou quand le plateau est redescendu a temperature ambiante.' }),
      el('button', { class: 'sm primary', text: '+ Webhook', onClick: () => openEditor() }),
    ]),
    list,
  ]);

  function openEditor(webhook) {
    const { body, read } = webhookForm(webhook || {}, printers);
    modal({
      title: webhook ? `Modifier ${webhook.name || 'le webhook'}` : 'Nouveau webhook',
      submitLabel: webhook ? 'Enregistrer' : 'Ajouter',
      render: (host) => host.append(body),
      onSubmit: async () => {
        const payload = read();
        if (!payload.url) {
          toast('L\'URL est obligatoire', 'warn');
          return false;
        }
        if (webhook) await api.patch(`api/webhooks/${webhook.id}`, payload);
        else await api.post('api/webhooks', payload);
        toast('Webhook enregistre', 'ok');
        refresh();
        return true;
      },
    });
  }

  function webhookCard(webhook) {
    return el('div', { class: 'card' }, [
      el('div', { class: 'row' }, [
        el('h3', { text: webhook.name || webhook.url }),
        el('span', { class: 'badge', text: EVENTS.find(([v]) => v === webhook.event)?.[1] || webhook.event }),
        webhook.enabled ? null : el('span', { class: 'badge offline', text: 'desactive' }),
        el('div', { style: 'flex:1' }),
        el('button', { class: 'sm', text: 'Modifier', onClick: () => openEditor(webhook) }),
        el('button', {
          class: 'sm danger', text: '✕',
          onClick: () => confirmDialog(`Supprimer le webhook ${webhook.name || webhook.url} ?`, async () => {
            await run(() => api.del(`api/webhooks/${webhook.id}`));
            refresh();
          }, { submitLabel: 'Supprimer' }),
        }),
      ]),
      el('div', { class: 'small muted mono', text: webhook.url }),
      el('div', { class: 'small muted', text: `Portee : ${scopeLabel(webhook, printers)}${webhook.event === 'bed_cold' ? ` · sous ${webhook.bed_cold_threshold}°C` : ''}` }),
      el('div', { class: 'small muted', text: `Dernier envoi : ${formatDate(webhook.last_fired_at)}` }),
      webhook.last_error
        ? el('div', { class: 'small', style: 'color:var(--err)', text: webhook.last_error })
        : null,
    ]);
  }

  async function refresh() {
    try {
      [printers] = await Promise.all([api.printers()]);
      const webhooksList = await api.get('api/webhooks');
      clear(list);
      if (!webhooksList.length) {
        list.append(emptyState('🪝', 'Aucun webhook configure.'));
        return;
      }
      for (const webhook of webhooksList) list.append(webhookCard(webhook));
    } catch (error) {
      toastError(error);
    }
  }

  refresh();
  return root;
}
