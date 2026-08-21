/** Reglages de l'instance. */
import { api, setToken } from '../api.js';
import { state } from '../store.js';
import { clear, el, field, formatBytes, formatDuration, run, toast, toastError } from '../ui.js';

export function settingsView() {
  const accountCard = el('div', { class: 'card' });
  const systemCard = el('div', { class: 'card' });

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [el('h1', { text: 'Reglages' })]),
    accountCard,
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
  ]);

  function renderAccount() {
    clear(accountCard).append(el('h3', { text: 'Compte' }));
    if (!state.info || !state.info.auth_enabled) {
      accountCard.append(el('p', { class: 'small muted', text: 'Authentification desactivee sur cette instance (ALLBUDY_AUTH_ENABLED=false).' }));
      return;
    }
    const current = el('input', { type: 'password' });
    const next = el('input', { type: 'password' });
    const confirm = el('input', { type: 'password' });
    accountCard.append(
      el('p', { class: 'small muted', text: `Connecte en tant que ${state.user ? state.user.username : '—'}.` }),
      field('Mot de passe actuel', current),
      el('div', { class: 'field-row' }, [
        field('Nouveau mot de passe', next),
        field('Confirmation', confirm),
      ]),
      el('div', { class: 'row' }, [
        el('button', {
          class: 'primary', text: 'Changer le mot de passe',
          onClick: async () => {
            if (next.value.length < 6) return toast('6 caracteres minimum', 'warn');
            if (next.value !== confirm.value) return toast('Les deux saisies different', 'warn');
            const result = await run(() => api.post('api/auth/password', {
              current_password: current.value, new_password: next.value,
            }));
            if (result) { current.value = next.value = confirm.value = ''; }
            return null;
          },
        }),
        el('button', {
          text: 'Se deconnecter',
          onClick: async () => {
            await api.post('api/auth/logout').catch(() => {});
            setToken('');
            window.location.reload();
          },
        }),
      ]),
    );
  }

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

  renderAccount();
  renderSystem();
  return root;
}
