import { escapeHtml as h, fmt, localDateTime } from '/static/common.js?v=20260810.1';

const contexts = new WeakMap();

export function mountCorrections(root, profile, scope) {
  if (!profile.corrections || !contexts.has(profile)) return;
  const actions = root.querySelector('.modal-actions');
  const container = root.querySelector('.dimension-breakdown-scroll') || actions?.parentElement || root;
  const html = correctionEditorHtml(profile, scope);
  if (container === actions?.parentElement) actions.insertAdjacentHTML('beforebegin', html);
  else container.insertAdjacentHTML('beforeend', html);
  bindCorrectionEditor(root, {...contexts.get(profile), scope});
}

function scopedTargets(state, scope) {
  if (scope?.level === 'color') return [{level: 'color', key: '', name: 'Color grade'}];
  return state.targets.filter(t => !scope || (t.level === scope.level && t.key === scope.key));
}

function rawLabel(value, criterion) {
  if (value == null) return 'No exact raw equivalent';
  if (criterion.inputType === 'duration') {
    const seconds = Number(value);
    return `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(2).replace(/\.?0+$/, '').padStart(2, '0')}`;
  }
  return `${Number(value)}${criterion.unit ? ` ${criterion.unit}` : ''}`;
}

export function correctionEditorHtml(profile, scope) {
  if (!profile.corrections) return '';
  const state = profile.corrections;
  const targets = scopedTargets(state, scope);
  const activity = scope?.level === 'activity' ? state.activities?.[scope.key] : null;
  const adjusted = activity && Object.keys(state.values[scope.key] || {}).length > 0;
  const history = activity ? activity.history : state.history;
  const originalAdmin = activity ? profile.adminEvaluations?.[scope.key] : null;
  const labels = {activity: 'Activity', dimension: 'Dimension', criterion: 'Criterion', color: 'Display'};
  return `<section class="correction-section">
    ${adjusted || originalAdmin ? `<div class="management-evaluation"><div><strong>Management evaluation</strong><span class="manual-grade-marker">Used for results${adjusted && activity.criteria.some(c => !c.adjusted) ? ' · Some criteria corrected' : ''}</span><p class="muted">${h(adjusted ? activity.author || 'Management' : originalAdmin.updatedBy)} · ${h(localDateTime(adjusted ? activity.updatedAt : originalAdmin.updatedAt))}</p></div><strong class="management-evaluation-score">${fmt(profile.result.activities[scope.key].score)} <small>/5</small></strong></div>` : ''}
    ${adjusted ? `<details class="management-evaluation-detail"><summary>View corrected criteria</summary><dl class="correction-equivalents">${activity.criteria.map(c => `<div><dt>${h(c.name)}</dt><dd>${fmt(profile.criteria?.[scope.key]?.[c.key]?.effectiveAverage)} /${c.maximum}${activity.converted && c.adjusted ? `<small class="manual-grade-marker">${h(rawLabel(c.rawValue, c))} · ${c.rawSource === 'entered' ? 'management entry' : 'calculated equivalent'}</small>` : ''}</dd></div>`).join('')}</dl></details>` : ''}
  <details class="correction-editor" id="managementCorrections">
    <summary>${adjusted || originalAdmin ? 'Edit grade correction' : 'Grade correction'}</summary>
    <p class="muted correction-explanation">${activity ? 'This management evaluation replaces evaluator grades for this activity. Original evaluations stay in the history.' : 'Change the effective result without changing original evaluator grades.'}</p>
    <form id="correctionForm"><fieldset disabled class="correction-fields">
      <label class="correction-target" ${scope ? 'hidden' : ''}>What would you like to change?<select id="correctionTarget">${targets.map((target, index) => `<option value="${index}">${h(labels[target.level])} — ${h(target.name)}</option>`).join('')}</select></label>
      ${activity ? `<div class="correction-mode" role="group" aria-label="Correction method"><label><input type="radio" name="correctionMode" value="single" checked>One activity grade</label><label><input type="radio" name="correctionMode" value="criteria">Grade each criterion</label></div>` : ''}
      <p id="correctionCurrent" class="correction-current"></p>
      <label id="correctionValueLabel">Target grade <span id="correctionScale" class="muted"></span><input id="correctionValue" type="number" step="any" inputmode="decimal"></label>
      <label id="correctionColorLabel" hidden>Color grade<select id="correctionColor">${state.bands.map(b => `<option value="${h(b.key)}">${h(b.name)}</option>`).join('')}</select></label>
      ${activity ? `<p id="singleGradeHelp" class="muted correction-wide">Every contributing criterion receives this grade.${activity.converted ? ' Equivalent results are calculated from the configured targets.' : ''}</p><div id="correctionCriteria" class="correction-criteria" hidden>${activity.criteria.map(c => {
        const fact = profile.criteria?.[scope.key]?.[c.key];
        const value = activity.converted ? (c.adjusted ? c.rawValue ?? '' : originalAdmin?.raw?.[c.key] ?? '') : fact?.effectiveAverage ?? '';
        const display = value;
        return `<label><span>${h(c.name)}<small class="muted">${activity.converted ? h(c.inputType === 'duration' ? 'Duration · mm:ss or seconds' : c.unit || 'Result') : `Grade /${c.maximum}`}</small></span><input class="criterion-correction-input" data-key="${h(c.key)}" aria-label="${h(c.name)}" type="${activity.converted && c.inputType === 'duration' ? 'text' : 'number'}" step="${activity.converted && c.inputType === 'integer' ? '1' : 'any'}" min="${activity.converted ? 0 : c.minimum}" ${activity.converted ? '' : `max="${c.maximum}"`} value="${h(display)}" inputmode="decimal" disabled></label>`;
      }).join('')}</div>` : ''}
      <label>Reason <span class="muted">(optional)</span><input id="correctionReason" maxlength="2000" autocomplete="off"></label>
      <div class="inline-actions"><button class="button primary" id="previewCorrection" type="submit">Preview change</button><button class="button secondary" id="restoreCorrection" type="button">Restore automatic</button></div>
    </fieldset></form>
    <div id="correctionStatus" role="status" aria-live="polite"></div>
    <div id="correctionPreview" class="correction-preview"></div>
    <div class="inline-actions"><button class="button primary" id="applyCorrection" hidden>Apply changes</button><button class="button ghost" id="cancelCorrection" hidden>Cancel</button><button class="button secondary" id="reloadCorrections" hidden>Reload latest</button></div>
    ${history.length ? `<details class="correction-history"><summary>Change history (${history.length})</summary>${history.map(event => `<article><div><strong>${h(event.actorName)}</strong><time>${h(localDateTime(event.createdAt))}</time></div><p>${h(event.reason || (event.after.operation?.action === 'restore' ? 'Restored automatic grades' : 'Grade correction'))}</p><small>Overall: ${fmt(event.before.overallScore)} → ${fmt(event.after.overallScore)}</small></article>`).join('')}</details>` : ''}
  </details></section>`;
}

export function bindCorrectionEditor(root, options) {
  const {profile, apiBase, request, reload, scope, openEditor, beforeChange = async () => true} = options;
  contexts.set(profile, options);
  const panel = root.querySelector('#managementCorrections');
  if (!panel) {
    const color = root.querySelector('.grade-orb');
    if (color && profile.result.manualColor) {
      color.querySelector('small').textContent = 'Color grade · Manual';
      color.title = `Automatic color: ${profile.result.automaticColor}`;
    }
    if (color && openEditor && !color.parentElement.querySelector('.edit-color-correction')) {
      color.insertAdjacentHTML('afterend', '<button type="button" class="link-button edit-color-correction">Edit color</button>');
      color.parentElement.querySelector('.edit-color-correction').onclick = () => openEditor({level: 'color'});
    }
    return;
  }
  const $ = selector => panel.querySelector(selector);
  const state = profile.corrections;
  const targets = scopedTargets(state, scope);
  const activity = scope?.level === 'activity' ? state.activities?.[scope.key] : null;
  const criterionMode = () => !!activity && $('input[name="correctionMode"]:checked').value === 'criteria';
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
    syncMode();
  };
  const syncMode = () => {
    if (!activity) return;
    const criteria = criterionMode();
    $('#correctionCriteria').hidden = !criteria;
    $('#correctionValueLabel').hidden = criteria;
    $('#singleGradeHelp').hidden = criteria;
    $('#correctionValue').disabled = criteria;
    $('#correctionValue').required = !criteria;
    panel.querySelectorAll('.criterion-correction-input').forEach(input => {
      input.disabled = !criteria; input.required = criteria;
    });
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
    if (action === 'set' && criterionMode()) {
      operation.value = null;
      operation[activity.converted ? 'rawValues' : 'criterionValues'] = Object.fromEntries(
        [...panel.querySelectorAll('.criterion-correction-input')].map(input => [input.dataset.key, input.value]));
    }
    setBusy(true); clearPreview(); $('#reloadCorrections').hidden = true; status('Preparing preview…');
    try {
      const data = await request(apiBase + '/preview', {method: 'POST', body: operation});
      pending = {...operation, inputFingerprint: data.inputFingerprint};
      $('#correctionPreview').innerHTML = `<h3>Review changes</h3><p>Overall: <strong>${fmt(data.before.overallScore)} → ${fmt(data.after.overallScore)}</strong> · Color: ${h(data.before.color)} → ${h(data.after.color)}</p>${data.warnings.map(w => `<p class="muted">${h(w)}</p>`).join('')}${data.affectedCriteria.length ? `<div class="table-wrap"><table><thead><tr><th>Criterion</th><th>Before</th><th>After</th></tr></thead><tbody>${data.affectedCriteria.map(c => `<tr><td>${h(c.after.name)}</td><td>${c.before.effectiveAverage == null ? 'Not graded' : fmt(c.before.effectiveAverage)}</td><td>${c.after.effectiveAverage == null ? 'Not graded' : fmt(c.after.effectiveAverage)} /${h(c.after.maximum)}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
      $('#applyCorrection').hidden = false; $('#cancelCorrection').hidden = false;
      if (activity) {
        $('#correctionPreview h3').insertAdjacentHTML('afterend', `<p>Activity grade: <strong>${fmt(data.before.activities[scope.key].score)} → ${fmt(data.after.activities[scope.key].score)} /5</strong></p>`);
      }
      if (activity?.converted && data.activityRawValues?.[scope.key]) {
        $('#correctionPreview').insertAdjacentHTML('beforeend', `<p class="muted">Calculated equivalents, not observed performances:</p><dl class="correction-equivalents">${activity.criteria.filter(c => c.key in data.activityRawValues[scope.key]).map(c => `<div><dt>${h(c.name)}</dt><dd>${h(rawLabel(data.activityRawValues[scope.key][c.key], c))}</dd></div>`).join('')}</dl>`);
      }
      status('Preview only — nothing has been saved.');
    } catch (error) { failure(error); }
    finally { setBusy(false); }
  };
  panel.addEventListener('toggle', () => { $('.correction-fields').disabled = !panel.open || busy; });
  $('#correctionTarget').onchange = updateTarget;
  $('#correctionForm').addEventListener('input', () => { clearPreview(); status(''); });
  panel.querySelectorAll('input[name="correctionMode"]').forEach(input => { input.onchange = syncMode; });
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
  syncMode();
}
