/** Reglages de l'instance: systeme, depots distants, journal. */
import { api } from '../api.js';
import { clear, el, formatBytes, formatDuration, toastError } from '../ui.js';
import { eventsSettingsSection } from './events.js';
import { storageSettingsSection } from './storage.js';

function section(title, content) {
  return el('div', { class: 'card' }, [
    el('h3', { text: title }),
    content,
  ]);
}

export function settingsView() {
  const systemCard = el('div', { class: 'card' });
  const eventsSection = eventsSettingsSection();

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [el('h1', { text: 'Reglages' })]),
    systemCard,
    el('div', { class: 'card' }, [
      el('h3', { text: 'Mode LAN Creality' }),
      el('p', { class: 'small muted', text: 'AllBudy ne parle qu\'a votre reseau local: aucune donnee ne transite par un service en ligne. Pour que vos machines repondent, activez le mode LAN / developpeur:' }),
      el('ul', { class: 'small muted' }, [
        el('li', { html: '<strong>K1 / K1C / K1 Max / K2 Plus</strong> — Reglages → Reseau → activer le <em>mode LAN</em> (et le mode developpeur pour exposer Moonraker sur le port 7125).' }),
        el('li', { html: '<strong>Ender-3 V3 KE / Plus</strong> — Moonraker est expose sur le port 7125 des que l\'imprimante est sur le reseau.' }),
        el('li', { html: '<strong>Firmware d\'origine sans Moonraker</strong> — utilisez le protocole <em>LAN Creality</em> (port 9999).' }),
      ]),
    ]),
    section('Depots distants', storageSettingsSection()),
    section('Journal', eventsSection),
  ]);

  async function renderSystem() {
    clear(systemCard).append(el('h3', { text: 'Systeme' }));
    try {
      const [info, stats] = await Promise.all([api.info(), api.stats()]);
      const rows = [
        ['Version', info.version],
        ['Plateforme', stats.platform],
        ['En service depuis', formatDuration(stats.uptime_s)],
        ['Imprimantes', `${stats.printers.online} en ligne / ${stats.printers.total}`],
        ['Bibliotheque', `${stats.library.files} fichiers (${formatBytes(stats.library.bytes)})`],
        ['Disque libre', formatBytes(stats.disk.free)],
        ['Intervalle d\'interrogation', `${info.poll_interval} s`],
        ['Taille max de televersement', `${info.max_upload_mb} Mo`],
        ['Attribution automatique', stats.dispatcher_enabled ? 'active' : 'suspendue'],
      ];
      systemCard.append(el('div', { class: 'table-wrap' }, [
        el('table', {}, [el('tbody', {}, rows.map(([key, value]) =>
          el('tr', {}, [el('th', { text: key }), el('td', { text: String(value) })])))]),
      ]));
      systemCard.append(el('div', { class: 'row', style: 'margin-top:.8rem' }, [
        el('a', { class: 'btn', href: 'docs', target: '_blank', text: 'Documentation de l\'API' }),
      ]));
    } catch (error) {
      toastError(error);
    }
  }

  renderSystem();
  // Le journal ecoute le bus temps reel: se desabonner en quittant Reglages.
  root.cleanup = eventsSection.cleanup;
  return root;
}
