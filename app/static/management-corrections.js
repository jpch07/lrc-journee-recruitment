import { escapeHtml as h, fmt, localDateTime } from '/static/common.js?v=20260810.1';

export function correctionEditorHtml(profile) {
  if (!profile.corrections) return '';
  const state = profile.corrections;
  const targets = [...state.targets, {level: 'color', name: 'Color grade'}];
  const labels = {activity: 'Activity', dimension: 'Dimension', criterion: 'Criterion', color: 'Display'};
  return `<details class="panel correction-editor" id="managementCorrections">
    <summary>Grade corrections <span class="muted">Review, adjust, or restore</span></summary>
    <p class="muted">Corrections change the effective result, not the original evaluator grades. Activity and dimension targets give every contributing criterion the same target grade.</p>
    <form id="correctionForm"><fieldset disabled class="correction-fields">
      <label class="correction-target">What would you like to change?<select id="correctionTarget">${targets.map((target, index) => `<option value="${index}">${h(labels[target.level])} — ${h(target.name)}</option>`).join('')}</select></label>
      <p id="correctionCurrent" class="correction-current"></p>
      <label id="correctionValueLabel">Target grade <span id="correctionScale" class="muted"></span><input id="correctionValue" type="number" step="any" inputmode="decimal"></label>
      <label id="correctionColorLabel" hidden>Color grade<select id="correctionColor">${state.bands.map(b => `<option value="${h(b.key)}">${h(b.name)}</option>`).join('')}</select></label>
      <label>Reason <span class="muted">(optional)</span><input id="correctionReason" maxlength="2000" autocomplete="off"></label>
      <div class="inline-actions"><button class="button primary" id="previewCorrection" type="submit">Preview change</button><button class="button secondary" id="restoreCorrection" type="button">Restore automatic</button></div>
    </fieldset></form>
    <div id="correctionStatus" role="status" aria-live="polite"></div>
    <div id="correctionPreview" class="correction-preview"></div>
    <div class="inline-actions"><button class="button primary" id="applyCorrection" hidden>Apply changes</button><button class="button ghost" id="cancelCorrection" hidden>Cancel</button><button class="button secondary" id="reloadCorrections" hidden>Reload latest</button></div>
    ${state.history.length ? `<details class="correction-history"><summary>Correction history (${state.history.length})</summary>${state.history.map(event => `<article><div><strong>${h(event.actorName)}</strong><time>${h(localDateTime(event.createdAt))}</time></div><p>${h(event.after.operation?.action || 'Correction')} · ${h(event.after.operation?.level || '')} ${h(event.after.operation?.key || '')}${event.reason ? ` — ${h(event.reason)}` : ''}</p><small>Overall: ${fmt(event.before.overallScore)} → ${fmt(event.after.overallScore)}${event.after.color ? ` · Color: ${h(event.after.color)}` : ''}</small><button class="button ghost small correction-undo" data-event="${h(event.id)}">Preview undo</button></article>`).join('')}</details>` : ''}
  </details>`;
}

export function bindCorrectionEditor(root, {profile, apiBase, request, reload, beforeChange = async () => true}) {
  const panel = root.querySelector('#managementCorrections');
  if (!panel) return;
  const $ = selector => panel.querySelector(selector);
  const state = profile.corrections;
  const targets = [...state.targets, {level: 'color', key: '', name: 'Color grade'}];
  const colorCaption = root.querySelector('.grade-orb small');
  if (colorCaption && profile.result.manualColor) {
    colorCaption.textContent = 'Color grade · Manual';
    colorCaption.title = `Automatic color: ${profile.result.automaticColor}`;
  }
  let pending = null;
  let busy = false;
  const selected = () => targets[Number($('#correctionTarget').value)];
  const status = message => { $('#correctionStatus').textContent = message; };
  const clearPreview = () => {
    pending = null;
    $('#correctionPreview').replaceChildren();
    $('#applyCorrection').hidden = true;
    $('#cancelCorrection').hidden = true;
  };
  const updateTarget = () => {
    clearPreview();
    const target = selected();
    const color = target.level === 'color';
    $('#correctionColorLabel').hidden = !color;
    $('#correctionValueLabel').hidden = color;
    $('#correctionValue').required = !color;
    if (color) {
      $('#correctionColor').value = profile.result.color;
      $('#correctionCurrent').textContent = `Automatic: ${profile.result.automaticColor || profile.result.color} · Effective: ${profile.result.color}${profile.result.manualColor ? ' (Manual)' : ''}`;
    } else {
      const input = $('#correctionValue');
      input.min = target.minimum; input.max = target.maximum;
      let automatic, effective, raw;
      if (target.level === 'criterion') {
        const fact = profile.criteria?.[target.activityKey]?.[target.key];
        automatic = fact?.automaticAverage; effective = fact?.effectiveAverage; raw = fact?.rawAverage;
      } else {
        const item = profile.result[target.level === 'activity' ? 'activities' : 'dimensions'][target.key];
        const scale = target.level === 'dimension' ? target.maximum : 1;
        automatic = (item.automaticScore ?? item.score) * scale; effective = item.score * scale;
      }
      input.value = effective == null ? '' : Number(effective.toFixed(6));
      $('#correctionScale').textContent = `(${target.minimum}–${target.maximum})`;
      $('#correctionCurrent').textContent = `${target.level === 'criterion' ? `Evaluator average: ${raw == null ? 'Not graded' : fmt(raw)} · ` : ''}Automatic: ${automatic == null ? 'Not graded' : fmt(automatic)} · Effective: ${effective == null ? 'Not graded' : fmt(effective)}`;
    }
  };
  const setBusy = value => {
    busy = value;
    $('.correction-fields').disabled = value || !panel.open;
    $('#applyCorrection').disabled = value;
    $('#cancelCorrection').disabled = value;
    $('#reloadCorrections').disabled = value;
    panel.querySelectorAll('.correction-undo').forEach(button => { button.disabled = value; });
  };
  const failure = error => {
    clearPreview();
    status(navigator.onLine ? error.message : 'Offline. Your entries are kept here. Reconnect and preview again.');
    $('#reloadCorrections').hidden = error.status !== 409;
  };
  const preview = async (action, eventId = null) => {
    if (busy) return;
    if (!await beforeChange()) { status('Resolve the unsaved General Assessment before making a correction.'); return; }
    const target = selected();
    const operation = {
      revision: state.revision, configurationSignature: state.configurationSignature,
      action, level: target.level, key: target.level === 'color' ? $('#correctionColor').value : target.key,
      activityKey: target.activityKey || null,
      value: action === 'set' && target.level !== 'color' ? $('#correctionValue').value : null,
      reason: $('#correctionReason').value, eventId,
    };
    setBusy(true); clearPreview(); $('#reloadCorrections').hidden = true; status('Preparing preview…');
    try {
      const data = await request(apiBase + '/preview', {method: 'POST', body: operation});
      pending = {...operation, inputFingerprint: data.inputFingerprint};
      $('#correctionPreview').innerHTML = `<h3>Review changes</h3><p>Overall: <strong>${fmt(data.before.overallScore)} → ${fmt(data.after.overallScore)}</strong> · Color: ${h(data.before.color)} → ${h(data.after.color)}</p>${data.warnings.map(w => `<p class="muted">${h(w)}</p>`).join('')}${data.affectedCriteria.length ? `<div class="table-wrap"><table><thead><tr><th>Criterion</th><th>Before</th><th>After</th></tr></thead><tbody>${data.affectedCriteria.map(c => `<tr><td>${h(c.after.name)}</td><td>${c.before.effectiveAverage == null ? 'Not graded' : fmt(c.before.effectiveAverage)}</td><td>${c.after.effectiveAverage == null ? 'Not graded' : fmt(c.after.effectiveAverage)} /${h(c.after.maximum)}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
      $('#applyCorrection').hidden = false; $('#cancelCorrection').hidden = false;
      status('Preview only — nothing has been saved.');
    } catch (error) { failure(error); }
    finally { setBusy(false); }
  };
  panel.addEventListener('toggle', () => { $('.correction-fields').disabled = !panel.open || busy; });
  $('#correctionTarget').onchange = updateTarget;
  $('#correctionForm').addEventListener('input', () => { clearPreview(); status(''); });
  $('#correctionForm').onsubmit = event => { event.preventDefault(); preview('set'); };
  $('#restoreCorrection').onclick = () => preview('restore');
  $('#cancelCorrection').onclick = () => { clearPreview(); status('Change cancelled. Nothing saved.'); };
  $('#reloadCorrections').onclick = () => reload();
  $('#applyCorrection').onclick = async () => {
    if (!pending || busy || !await beforeChange()) return;
    setBusy(true); status('Saving correction…');
    try {
      await request(apiBase, {method: 'PUT', body: pending});
      clearPreview(); status('Saved. Refreshing grades…');
      await reload();
    } catch (error) { failure(error); }
    finally { if (panel.isConnected) setBusy(false); }
  };
  panel.querySelectorAll('.correction-undo').forEach(button => { button.onclick = () => preview('undo', button.dataset.event); });
  panel.addEventListener('keydown', event => { if (event.key === 'Escape' && !busy) { clearPreview(); status(''); } });
  updateTarget();
}
