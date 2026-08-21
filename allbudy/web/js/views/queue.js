/** File d'attente: ordre, etat, diagnostic d'attribution. */
import { api } from '../api.js';
import { on, printerState } from '../store.js';
import {
  clear, confirmDialog, el, emptyState, formatDate, jobBadge, modal, run, toastError,
} from '../ui.js';

const ACTIVE = new Set(['assigned', 'sending', 'printing', 'paused']);

export function queueView(navigate) {
  const queueBox = el('div', { class: 'card' });
  const activeBox = el('div', { class: 'card' });
  const historyBox = el('div', { class: 'card' });
  const dispatcherButton = el('button', {});
  let printersById = new Map();
  let dispatcherEnabled = true;

  const root = el('div', {}, [
    el('div', { class: 'page-head' }, [
      el('h1', { text: 'File d\'attente' }),
      el('div', { class: 'spacer' }),
      dispatcherButton,
      el('button', { text: 'Actualiser', onClick: refresh }),
    ]),
    activeBox,
    queueBox,
    historyBox,
  ]);

  function printerName(id) {
    const printer = printersById.get(id);
    return printer ? printer.name : (id ? `#${id}` : '—');
  }

  function renderDispatcher() {
    clear(dispatcherButton);
    dispatcherButton.className = dispatcherEnabled ? '' : 'primary';
    dispatcherButton.textContent = dispatcherEnabled
      ? 'Suspendre l\'attribution'
      : 'Reprendre l\'attribution';
    dispatcherButton.onclick = async () => {
      await run(() => api.post(`api/jobs/dispatcher?enabled=${!dispatcherEnabled}`));
      refresh();
    };
  }

  async function explain(job) {
    modal({
      title: `Pourquoi « ${job.name} » attend`,
      submitLabel: null,
      wide: true,
      render: async (body) => {
        body.append(el('p', { class: 'small muted', text: 'Evaluation de chaque imprimante pour ce travail.' }));
        try {
          const report = await api.get(`api/jobs/${job.id}/match`);
          if (!report.length) {
            body.append(el('p', { text: 'Aucune imprimante configuree.' }));
            return;
          }
          const tbody = el('tbody', {}, report.map((row) => el('tr', {}, [
            el('td', { text: row.printer }),
            el('td', {}, [el('span', {
              class: `badge ${row.ok ? 'printing' : 'offline'}`,
              text: row.ok ? 'compatible' : 'non',
            })]),
            el('td', { class: 'small muted', text: row.reason }),
          ])));
          body.append(el('div', { class: 'table-wrap' }, [el('table', {}, [
            el('thead', {}, [el('tr', {}, [
              el('th', { text: 'Imprimante' }), el('th', { text: 'Verdict' }), el('th', { text: 'Detail' }),
            ])]),
            tbody,
          ])]));
        } catch (error) {
          toastError(error);
        }
      },
    });
  }

  function jobRow(job, { draggable = false } = {}) {
    const live = job.printer_id ? printerState(job.printer_id) : null;
    const progress = job.status === 'printing' && live ? live.status.progress : job.progress;

    const meta = [];
    if (job.copies > 1) meta.push(`exemplaire ${job.copies_done + 1}/${job.copies}`);
    if (job.priority) meta.push(`priorite ${job.priority}`);
    if (job.required_material) meta.push(job.required_material);
    if (job.printer_id) meta.push(printerName(job.printer_id));
    if (job.error) meta.push(job.error);

    const row = el('div', { class: 'queue-item', draggable }, [
      draggable ? el('span', { class: 'handle', text: '⠿' }) : null,
      job.required_color
        ? el('span', { class: 'chip', style: `background:${job.required_color};width:14px;height:14px;border-radius:50%;border:1px solid rgba(255,255,255,.25)` })
        : null,
      el('div', { class: 'grow' }, [
        el('div', { class: 'truncate', text: job.name }),
        el('div', { class: 'small muted truncate', text: meta.join(' · ') || formatDate(job.created_at) }),
        ACTIVE.has(job.status)
          ? el('div', { class: 'progress-line', style: 'margin-top:.35rem' }, [
              el('div', { style: `width:${Math.min(100, progress || 0)}%` }),
            ])
          : null,
      ]),
      jobBadge(job.status),
      el('div', { class: 'row', style: 'gap:.3rem' }, [
        job.status === 'queued'
          ? el('button', { class: 'sm ghost', text: '?', title: 'Pourquoi ce travail attend', onClick: () => explain(job) })
          : null,
        job.printer_id && ACTIVE.has(job.status)
          ? el('button', { class: 'sm', text: 'Voir', onClick: () => navigate(`printer/${job.printer_id}`) })
          : null,
        ['completed', 'failed', 'cancelled'].includes(job.status)
          ? el('button', { class: 'sm', text: 'Relancer', onClick: () => run(async () => { await api.post(`api/jobs/${job.id}/requeue`); refresh(); }) })
          : null,
        !['completed', 'cancelled'].includes(job.status)
          ? el('button', {
              class: 'sm danger', text: 'Annuler',
              onClick: () => confirmDialog(`Annuler « ${job.name} » ?`, async () => {
                await run(() => api.post(`api/jobs/${job.id}/cancel`));
                refresh();
              }, { submitLabel: 'Annuler le travail' }),
            })
          : el('button', {
              class: 'sm ghost', text: '✕', title: 'Retirer de l\'historique',
              onClick: () => run(async () => { await api.del(`api/jobs/${job.id}`); refresh(); }),
            }),
      ]),
    ]);
    return row;
  }

  /** Reordonnancement par glisser-deposer, envoye a l'API au depot. */
  function makeSortable(container, jobs) {
    let dragged = null;
    container.addEventListener('dragstart', (event) => {
      dragged = event.target.closest('.queue-item');
      if (dragged) dragged.classList.add('dragging');
    });
    container.addEventListener('dragend', async () => {
      if (!dragged) return;
      dragged.classList.remove('dragging');
      dragged = null;
      const order = [...container.querySelectorAll('.queue-item')].map((node) => Number(node.dataset.jobId));
      await run(() => api.post('api/jobs/reorder', { job_ids: order }));
      refresh();
    });
    container.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (!dragged) return;
      const target = event.target.closest('.queue-item');
      if (!target || target === dragged) return;
      const bounds = target.getBoundingClientRect();
      const after = event.clientY > bounds.top + bounds.height / 2;
      container.insertBefore(dragged, after ? target.nextSibling : target);
    });
    return jobs;
  }

  async function refresh() {
    try {
      const [jobs, printers, stats] = await Promise.all([
        api.jobs('?limit=400'),
        api.printers(),
        api.get('api/jobs/stats'),
      ]);
      printersById = new Map(printers.map((p) => [p.id, p]));
      dispatcherEnabled = stats.dispatcher_enabled;
      renderDispatcher();

      const active = jobs.filter((job) => ACTIVE.has(job.status));
      const queued = jobs.filter((job) => job.status === 'queued');
      const done = jobs.filter((job) => ['completed', 'failed', 'cancelled'].includes(job.status));

      clear(activeBox).append(el('h3', { text: `En cours (${active.length})` }));
      if (!active.length) activeBox.append(el('p', { class: 'small muted', text: 'Aucune impression en cours.' }));
      else for (const job of active) activeBox.append(jobRow(job));

      clear(queueBox).append(el('h3', { text: `En attente (${queued.length})` }));
      if (!queued.length) {
        queueBox.append(emptyState('📋', 'File vide. Ajoutez un fichier depuis la bibliotheque.'));
      } else {
        const list = el('div');
        for (const job of queued) {
          const row = jobRow(job, { draggable: true });
          row.dataset.jobId = job.id;
          list.append(row);
        }
        makeSortable(list, queued);
        queueBox.append(
          el('p', { class: 'small muted', text: 'Glissez-deposez pour changer l\'ordre. Les priorites elevees passent devant.' }),
          list,
        );
      }

      clear(historyBox).append(el('h3', { text: `Historique (${done.length})` }));
      if (!done.length) historyBox.append(el('p', { class: 'small muted', text: 'Rien encore.' }));
      else for (const job of done.slice(0, 40)) historyBox.append(jobRow(job));
    } catch (error) {
      toastError(error);
    }
  }

  // Le serveur previent des changements: pas besoin d'interroger en boucle.
  const unsubscribers = [
    on('job.updated', refresh),
    on('job.created', refresh),
    on('job.removed', refresh),
  ];
  root.cleanup = () => unsubscribers.forEach((fn) => fn());

  refresh();
  return root;
}
