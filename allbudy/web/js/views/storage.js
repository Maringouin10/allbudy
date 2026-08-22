/** Depots distants FTP / FTPS / SFTP: configuration (embarque dans Reglages).
 *
 * La navigation dans les dossiers d'un depot se fait desormais depuis
 * l'ecran Fichiers (sidebar "Depots"): cet ecran ne gere que la
 * configuration (ajout, identifiants, test, synchronisation complete).
 */
import { api } from '../api.js';
import {
  clear, confirmDialog, el, emptyState, field, formatDate, modal, run, toast, toastError,
} from '../ui.js';

const KINDS = [['sftp', 'SFTP (SSH)'], ['ftp', 'FTP'], ['ftps', 'FTPS (FTP + TLS)']];
const DEFAULT_PORTS = { sftp: 22, ftp: 21, ftps: 21 };

function storageForm(storage = {}) {
  const fields = {
    name: el('input', { value: storage.name || '', placeholder: 'NAS atelier' }),
    kind: el('select', {}, KINDS.map(([value, label]) =>
      el('option', { value, text: label, selected: (storage.kind || 'sftp') === value }))),
    host: el('input', { value: storage.host || '', placeholder: '192.168.1.10' }),
    port: el('input', { type: 'number', value: storage.port ?? '' }),
    username: el('input', { value: storage.username || '' }),
    password: el('input', {
      type: 'password',
      placeholder: storage.has_password ? '•••••• (inchange)' : '',
    }),
    remote_path: el('input', { value: storage.remote_path || '/', placeholder: '/volume1/gcode' }),
    enabled: el('input', { type: 'checkbox', checked: storage.enabled !== false }),
    auto_import: el('input', { type: 'checkbox', checked: !!storage.auto_import }),
    sync_interval: el('input', { type: 'number', min: 60, value: storage.sync_interval ?? 900 }),
  };

  fields.kind.addEventListener('change', () => {
    if (!fields.port.value || Object.values(DEFAULT_PORTS).includes(Number(fields.port.value))) {
      fields.port.value = DEFAULT_PORTS[fields.kind.value];
    }
  });

  const body = el('div', {}, [
    field('Nom', fields.name),
    el('div', { class: 'field-row' }, [field('Protocole', fields.kind), field('Port', fields.port)]),
    field('Hote', fields.host),
    el('div', { class: 'field-row' }, [
      field('Utilisateur', fields.username),
      field('Mot de passe', fields.password),
    ]),
    field('Repertoire distant', fields.remote_path),
    el('div', { class: 'check' }, [fields.enabled, el('label', { text: 'Depot actif' })]),
    el('div', { class: 'check' }, [fields.auto_import, el('label', { text: 'Importer automatiquement les nouveaux fichiers' })]),
    field('Intervalle de synchro (s)', fields.sync_interval),
  ]);

  const read = () => {
    const payload = {
      name: fields.name.value.trim(),
      kind: fields.kind.value,
      host: fields.host.value.trim(),
      port: fields.port.value ? Number(fields.port.value) : null,
      username: fields.username.value.trim(),
      remote_path: fields.remote_path.value.trim() || '/',
      enabled: fields.enabled.checked,
      auto_import: fields.auto_import.checked,
      sync_interval: Number(fields.sync_interval.value) || 900,
    };
    // Champ laisse vide a l'edition = mot de passe inchange.
    if (fields.password.value || !storage.id) payload.password = fields.password.value;
    return payload;
  };

  return { body, read };
}

/** Section "Depots" embarquee dans Reglages: configuration uniquement. */
export function storageSettingsSection() {
  const list = el('div', { class: 'col' });
  const root = el('div', {}, [
    el('div', { class: 'row', style: 'margin-bottom:.5rem' }, [
      el('p', { class: 'small muted', style: 'flex:1;margin:0', text: 'Recuperez vos fichiers tranches depuis un NAS ou un serveur de l\'atelier, ou renvoyez-y la bibliotheque pour sauvegarde. Parcourez leur contenu depuis l\'ecran Fichiers.' }),
      el('button', { class: 'sm primary', text: '+ Depot', onClick: () => openEditor() }),
    ]),
    list,
  ]);

  function openEditor(storage) {
    const { body, read } = storageForm(storage || {});
    modal({
      title: storage ? `Modifier ${storage.name}` : 'Nouveau depot',
      submitLabel: storage ? 'Enregistrer' : 'Ajouter',
      render: (host) => host.append(body),
      onSubmit: async () => {
        const payload = read();
        if (!payload.name || !payload.host) {
          toast('Nom et hote sont obligatoires', 'warn');
          return false;
        }
        if (storage) await api.patch(`api/storage/${storage.id}`, payload);
        else await api.post('api/storage', payload);
        toast('Depot enregistre', 'ok');
        refresh();
        return true;
      },
    });
  }

  function storageCard(storage) {
    return el('div', { class: 'card' }, [
      el('div', { class: 'row' }, [
        el('h3', { text: storage.name }),
        el('span', { class: 'badge', text: storage.kind.toUpperCase() }),
        storage.auto_import ? el('span', { class: 'badge printing', text: 'auto' }) : null,
        storage.enabled ? null : el('span', { class: 'badge offline', text: 'desactive' }),
        el('div', { style: 'flex:1' }),
        el('button', { class: 'sm', text: 'Tester', onClick: () => run(() => api.post(`api/storage/${storage.id}/test`)) }),
        el('button', {
          class: 'sm primary', text: 'Synchroniser',
          onClick: () => run(async () => {
            const result = await api.post(`api/storage/${storage.id}/sync`);
            toast(`${result.imported.length} fichier(s) importe(s) sur ${result.listed} vus`, 'ok');
            refresh();
          }),
        }),
        el('button', { class: 'sm', text: 'Modifier', onClick: () => openEditor(storage) }),
        el('button', {
          class: 'sm danger', text: '✕',
          onClick: () => confirmDialog(`Supprimer le depot ${storage.name} ?`, async () => {
            await run(() => api.del(`api/storage/${storage.id}`));
            refresh();
          }, { submitLabel: 'Supprimer' }),
        }),
      ]),
      el('div', { class: 'small muted mono', text: `${storage.username ? `${storage.username}@` : ''}${storage.host}:${storage.port}${storage.remote_path}` }),
      el('div', { class: 'small muted', text: `Derniere synchro: ${formatDate(storage.last_sync)}` }),
      storage.last_error
        ? el('div', { class: 'small', style: 'color:var(--err)', text: storage.last_error })
        : null,
    ]);
  }

  async function refresh() {
    try {
      const storages = await api.storages();
      clear(list);
      if (!storages.length) {
        list.append(emptyState('🗄️', 'Aucun depot configure.'));
        return;
      }
      for (const storage of storages) list.append(storageCard(storage));
    } catch (error) {
      toastError(error);
    }
  }

  refresh();
  return root;
}
