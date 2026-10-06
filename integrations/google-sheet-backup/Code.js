/* Private STANDALONE Apps Script. Never bind this to a publicly editable sheet.
 * Script properties: BACKUP_SECRET (32+ characters), WORKSPACE_ID.
 * Enable the Sheets v4 advanced service. No Drive files/folders are created.
 */
const BACKUP_SHEET = '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0';
const LEASE_SECONDS = 900;
const OWNED_KEY = 'evalday_backup_run';
const PUBLISHED_KEY = 'evalday_backup_published';
const LAYOUT_PROTECTION = 'Evalday interactive presentation selectors';
const PROFILE_LAYOUT_KEY = 'evalday_profile_layout_v1';
const PRESENTATIONS = {
  'results-v1': {name:'Results', finalTitle:'Results'},
  'recruit-profiles-v1': {name:'Recruit Profiles', finalTitle:'Recruit Profiles'},
};

function fail(code) { throw new Error(code); }
function hex(bytes) { return bytes.map(b => ((b + 256) % 256).toString(16).padStart(2, '0')).join(''); }
function hash(value) { return hex(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, value, Utilities.Charset.UTF_8)); }
function hashBytes(value) { return hex(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, value)); }
function secureEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return mismatch === 0;
}
function validateEnvelope(e, secret, now, sign) {
  if (typeof secret !== 'string' || secret.length < 32 || !e || !Number.isInteger(e.timestamp) || Math.abs(now-e.timestamp)>300 ||
      !/^[a-f0-9]{32}$/.test(e.nonce || '') || typeof e.payload !== 'string' || e.payload.length > 2000000) fail('AUTH');
  const expected = sign(`${e.timestamp}.${e.nonce}.${e.payload}`);
  if (!secureEqual(expected, e.signature)) fail('AUTH');
  return e.payload;
}
function validateDestination(id) { if (id !== BACKUP_SHEET) fail('DESTINATION'); }
function checkLease(state, runId, now) {
  if (!state || state.expires < now) fail('EXPIRED');
  if (state.runId !== runId) fail('BUSY');
}
function checkSequence(state, sequence, digest) {
  if (sequence === state.next - 1 && digest === state.lastDigest) return 'retry';
  if (sequence !== state.next) fail('SEQUENCE');
  return 'next';
}
function finalTitle(descriptor) {
  if (!descriptor || typeof descriptor.name !== 'string') fail('TABS');
  if (!descriptor.presentation) return `Backup - ${descriptor.name}`;
  const expected = PRESENTATIONS[descriptor.presentation];
  if (!expected || descriptor.version !== 1 || descriptor.name !== expected.name ||
      descriptor.finalTitle !== expected.finalTitle || descriptor.hidden) fail('TABS');
  return expected.finalTitle;
}
function validateTabs(tabs, existingCells) {
  if (!Array.isArray(tabs) || !tabs.length || tabs.length > 60) fail('TABS');
  const names = new Set();
  let cells = 0;
  tabs.forEach(t => {
    const presentation = !!t.presentation;
    const maximumColumns = presentation ? 128 : 60;
    if (typeof t.name !== 'string' || !/^[A-Za-z0-9 _-]{1,60}$/.test(t.name) || names.has(t.name) ||
        !Number.isInteger(t.rows) || t.rows < 1 || !Number.isInteger(t.cols) || t.cols < 1 || t.cols > maximumColumns) fail('TABS');
    finalTitle(t);
    names.add(t.name); cells += t.rows*t.cols;
  });
  // Conservative ceiling; never depend on a newly increased account limit.
  if (cells + existingCells > 10000000) fail('CAPACITY');
  return cells;
}
function verifyRows(expected, actual) {
  if (expected.length !== actual.length) fail('VERIFY');
  expected.forEach((row, r) => row.forEach((cell, c) => {
    if (String(cell) !== String(actual[r][c] == null ? '' : actual[r][c])) fail('VERIFY');
  }));
}
function jsonOutput(value) { return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON); }

function doPost(event) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return jsonOutput({ok:false, code:'BUSY'});
  try {
    const props = PropertiesService.getScriptProperties();
    const now = Math.floor(Date.now()/1000);
    const envelope = JSON.parse(event.postData.contents);
    const secret = props.getProperty('BACKUP_SECRET');
    const encoded = validateEnvelope(envelope, secret, now, v => hex(Utilities.computeHmacSha256Signature(v, secret)));
    const nonceKey = `nonce_${envelope.nonce}`;
    if (props.getProperty(nonceKey)) fail('AUTH');
    Object.entries(props.getProperties()).forEach(([key,value]) => {
      if (key.startsWith('nonce_') && Number(value) < now) props.deleteProperty(key);
    });
    props.setProperty(nonceKey, String(now+300));
    const request = JSON.parse(Utilities.newBlob(Utilities.base64Decode(encoded)).getDataAsString('UTF-8'));
    validateDestination(request.spreadsheetId);
    if (!props.getProperty('WORKSPACE_ID') || request.workspaceId !== props.getProperty('WORKSPACE_ID')) fail('DESTINATION');
    return jsonOutput({ok:true, result: dispatch(request, props, now)});
  } catch (error) {
    const codes = ['AUTH','DESTINATION','BUSY','EXPIRED','SEQUENCE','CAPACITY','COLLISION','VERIFY','TABS','INCOMPLETE'];
    return jsonOutput({ok:false, code:codes.includes(error.message) ? error.message : 'GOOGLE'});
  } finally { lock.releaseLock(); }
}

function meta(sheet, key) { return sheet.getDeveloperMetadata().find(m => m.getKey() === key); }
function published(ss) {
  const marker = ss.getDeveloperMetadata().find(m => m.getKey() === PUBLISHED_KEY);
  return marker ? JSON.parse(marker.getValue()) : null;
}
function saveState(props, state) {
  const value = JSON.stringify(state);
  if (Utilities.newBlob(value).getBytes().length > 8500) fail('CAPACITY');
  props.setProperty('RUN', value);
}
function dispatch(req, props, now) {
  let state = JSON.parse(props.getProperty('RUN') || 'null');
  const ss = SpreadsheetApp.openById(BACKUP_SHEET);
  const complete = published(ss);
  if (req.action === 'status') return {state: state && state.expires >= now && state.runId !== (complete || {}).runId ? 'busy' : 'ready', lastComplete:complete};
  if (!/^[a-f0-9]{32}$/.test(req.runId || '')) fail('SEQUENCE');
  if (req.action === 'begin') {
    if (state && state.expires >= now && state.runId !== (complete || {}).runId) {
      checkLease(state, req.runId, now);
      return {next:state.next};
    }
    // Only remove abandoned staging owned by this integration, never published data.
    const abandoned = ss.getSheets().filter(s => {
      const tag = meta(s, OWNED_KEY);
      return tag && tag.getValue() !== (complete || {}).runId;
    });
    if (abandoned.length) Sheets.Spreadsheets.batchUpdate({requests:abandoned.map(s => ({deleteSheet:{sheetId:s.getSheetId()}}))}, BACKUP_SHEET);
    state = {runId:req.runId, expires:now+LEASE_SECONDS, next:0, lastDigest:'', tabs:[], images:0, verifiedPhotos:0};
    saveState(props, state);
    return {next:0};
  }
  if (req.action === 'abort') {
    if (state && state.runId === req.runId && state.runId !== (complete || {}).runId) props.deleteProperty('RUN');
    return {state:'cancelled'};
  }
  if (req.action !== 'apply') fail('SEQUENCE');
  if (complete && complete.runId === req.runId) return {state:'complete', lastComplete:complete};
  checkLease(state, req.runId, now);
  const digest = hash(JSON.stringify(req.operation));
  if (checkSequence(state, req.sequence, digest) === 'retry') return {next:state.next};
  applyOperation(req.operation, state, ss);
  state.next++;
  state.lastDigest = digest;
  state.expires = now + LEASE_SECONDS;
  saveState(props, state);
  return {next:state.next, state:req.operation.kind === 'publish' ? 'complete' : 'running', lastComplete:published(ss)};
}

function stageTitle(state, name) { return `_stage_${state.runId.slice(0,12)}_${name}`; }
function stage(ss, state, name) {
  const descriptor = state.tabs.find(t => t.name === name);
  if (!descriptor) fail('TABS');
  const sheet = ss.getSheetById(descriptor.id);
  if (!sheet || !meta(sheet, OWNED_KEY) || meta(sheet, OWNED_KEY).getValue() !== state.runId) fail('VERIFY');
  return sheet;
}
function columnLetter(column) {
  let value = '';
  while (column > 0) { column--; value = String.fromCharCode(65 + column % 26) + value; column = Math.floor(column / 26); }
  return value;
}
function exactKeys(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some(key=>!allowed.includes(key)) || allowed.some(key=>!(key in value))) fail('TABS');
}
function boundedInteger(value, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) fail('TABS');
  return value;
}
function boundedBlock(block, tab) {
  exactKeys(block,['startRow','endRow','startCol','endCol']);
  boundedInteger(block.startRow,1,tab.rows); boundedInteger(block.endRow,block.startRow,tab.rows);
  boundedInteger(block.startCol,1,tab.cols); boundedInteger(block.endCol,block.startCol,tab.cols);
  return block;
}
function validateBandStyles(styles) {
  if (!Array.isArray(styles) || styles.length<1 || styles.length>10) fail('TABS');
  const labels=new Set();
  styles.forEach(style=>{
    exactKeys(style,['label','background','font']);
    if (typeof style.label!=='string' || !style.label.length || style.label.length>80 || /[\u0000-\u001f]/.test(style.label) ||
        labels.has(style.label) || typeof style.background!=='string' || !/^#[0-9A-F]{6}$/i.test(style.background) ||
        typeof style.font!=='string' || !/^#[0-9A-F]{6}$/i.test(style.font)) fail('TABS');
    labels.add(style.label);
  });
  return styles;
}
function validateLayout(op, tab) {
  if (!op || op.version !== 1 || op.tab !== tab.name || op.presentation !== tab.presentation || !op.layout) fail('TABS');
  const l = op.layout;
  if (tab.presentation === 'results-v1') {
    exactKeys(l,['selectorCells','headerRow','visibleStartRow','visibleCapacity','frozenRows','helperStartCol','tabColor','bandStyles','columnWidths','blocks']);
    if (JSON.stringify(l.selectorCells)!==JSON.stringify(['B3','E3']) || l.headerRow!==5 || l.visibleStartRow!==6 ||
        l.frozenRows!==5 || l.helperStartCol!==13 || l.tabColor!=='GREEN') fail('TABS');
    boundedInteger(l.visibleCapacity,1,tab.rows-5);
    validateBandStyles(l.bandStyles);
    if (!Array.isArray(l.columnWidths) || l.columnWidths.length!==10 || l.columnWidths.some(v=>!Number.isInteger(v)||v<40||v>500)) fail('TABS');
    exactKeys(l.blocks,['results','scopeOptions','viewOptions']);
    const data=boundedBlock(l.blocks.results,tab), scopes=boundedBlock(l.blocks.scopeOptions,tab), views=boundedBlock(l.blocks.viewOptions,tab);
    if (data.startCol!==13 || data.endCol!==25 || scopes.startCol!==26 || scopes.endCol!==26 ||
        views.startCol!==27 || views.endCol!==27 || data.startRow!==1 || scopes.startRow!==1 || views.startRow!==1) fail('TABS');
  } else if (tab.presentation === 'recruit-profiles-v1') {
    exactKeys(l,['selectorCells','profileKeyCell','imageAnchor','frozenRows','helperStartCol','tabColor','bandStyles','columnWidths','expectedPreviewCount',
      'dimensionCount','activityCount','factorCount','evaluatorCapacity','criterionCapacity','auditCapacity','sectionRows','charts','blocks']);
    if (JSON.stringify(l.selectorCells)!==JSON.stringify(['B3','E3']) || l.profileKeyCell!=='H3' || l.imageAnchor!=='J3' ||
        l.frozenRows!==7 || l.helperStartCol!==27 || l.tabColor!=='YELLOW') fail('TABS');
    if (!Array.isArray(l.columnWidths) || l.columnWidths.length!==12 || l.columnWidths.some(v=>!Number.isInteger(v)||v<20||v>500)) fail('TABS');
    validateBandStyles(l.bandStyles);
    boundedInteger(l.expectedPreviewCount,0,5000); boundedInteger(l.dimensionCount,1,40); boundedInteger(l.activityCount,1,40);
    boundedInteger(l.factorCount,0,40); boundedInteger(l.evaluatorCapacity,1,500); boundedInteger(l.criterionCapacity,1,116); boundedInteger(l.auditCapacity,1,500);
    exactKeys(l.sectionRows,['dimension','dimensionHeader','dimensionStart','activity','activityHeader','activityStart','general','generalStart',
      'evaluator','evaluatorHeader','evaluatorStart','criterion','criterionHeader','criterionStart','audit','auditHeader','auditStart']);
    Object.values(l.sectionRows).forEach(v=>boundedInteger(v,1,tab.rows));
    if (!Array.isArray(l.charts) || l.charts.length!==2) fail('TABS');
    l.charts.forEach((chart,index)=>{
      exactKeys(chart,['startRow','endRow','labelCol','valueCol','anchor','maximum']);
      boundedInteger(chart.startRow,1,tab.rows); boundedInteger(chart.endRow,chart.startRow,tab.rows);
      if (chart.labelCol!==1 || chart.valueCol!==2 || chart.anchor!==(index?'G26':'G8') || !Number.isInteger(chart.maximum) || chart.maximum<1 || chart.maximum>100) fail('TABS');
    });
    exactKeys(l.blocks,['summaries','dimensions','activities','evaluators','criteria','audit','scopeOptions','profileOptions','dependentOptions','previews']);
    Object.values(l.blocks).forEach(block=>boundedBlock(block,tab));
    const expectedWidths={summaries:21+l.factorCount,dimensions:6,activities:6,evaluators:7,criteria:9,audit:7,scopeOptions:1,profileOptions:3,dependentOptions:2,previews:5};
    Object.entries(expectedWidths).forEach(([key,width])=>{if(l.blocks[key].endCol-l.blocks[key].startCol+1!==width)fail('TABS')});
    let nextColumn=27;
    ['summaries','dimensions','activities','evaluators','criteria','audit','scopeOptions','profileOptions','dependentOptions','previews'].forEach(key=>{
      const block=l.blocks[key];
      if(block.startRow!==1 || block.startCol!==nextColumn) fail('TABS');
      nextColumn=block.endCol+1;
    });
    if (l.blocks.previews.endCol!==tab.cols || l.blocks.dependentOptions.endRow-l.blocks.dependentOptions.startRow!==l.expectedPreviewCount) fail('TABS');
  } else fail('TABS');
  return l;
}
function blockRange(block, offset, startRow) {
  const column = columnLetter(block.startCol + offset);
  const first=startRow || (block.endRow>block.startRow ? block.startRow+1 : block.startRow);
  return `$${column}$${first}:$${column}$${block.endRow}`;
}
function scalarLookup(key, keyRange, valueRange, blank) {
  const fallback=(blank===undefined?'—':blank).replace(/"/g,'""');
  const lookup=`XLOOKUP(${key},${keyRange},${valueRange})`;
  return `=IFERROR(IF(${lookup}="","${fallback}",${lookup}),"${fallback}")`;
}
function numericScalarLookup(key, keyRange, valueRange) {
  const lookup=`XLOOKUP(${key},${keyRange},${valueRange})`;
  return `=IFERROR(LET(v,${lookup},IF(v="","",VALUE(v))),"")`;
}
function indexedFilter(key, keyRange, valueRange, ordinal) {
  return `=IFERROR(INDEX(FILTER(${valueRange},${keyRange}=${key}),${ordinal}),"")`;
}
function applyFormulaRanges(sheet, ranges) {
  ranges.forEach(item=>sheet.getRange(item.row,item.col,item.values.length,item.values[0].length).setFormulas(item.values));
}
function verifyFormulaRanges(sheet, ranges) {
  ranges.forEach(item=>{
    const actual=sheet.getRange(item.row,item.col,item.values.length,item.values[0].length).getFormulas();
    if(JSON.stringify(actual)!==JSON.stringify(item.values)) fail('VERIFY');
  });
}
function resultsFormulaRanges(layout) {
  const block=layout.blocks.results,keyRange=blockRange(block,12),start=layout.visibleStartRow;
  const values=[];
  for(let index=0;index<layout.visibleCapacity;index++) {
    const row=start+index,ordinal=`ROWS($A$${start}:$A${row})`,key=`$B$3&"|"&$E$3&"|"&${ordinal}`;
    const formulas=[];
    for(let column=1;column<=10;column++) formulas.push(scalarLookup(key,keyRange,blockRange(block,column+1),''));
    values.push(formulas);
  }
  return [{row:start,col:1,values}];
}
function setListValidation(sheet, cell, block) {
  const target=sheet.getRange(cell);
  target.clearDataValidations();
  if (block.endRow<=block.startRow) return;
  const source=sheet.getRange(block.startRow+1,block.startCol,block.endRow-block.startRow,1);
  target.setDataValidation(SpreadsheetApp.newDataValidation().requireValueInRange(source,true).setAllowInvalid(false)
    .setHelpText('Choose a value from the managed backup list.').build());
}
function textRule(sheet, range, text, background, font) {
  return SpreadsheetApp.newConditionalFormatRule().whenTextEqualTo(text).setBackground(background).setFontColor(font)
    .setRanges([sheet.getRange(range)]).build();
}
function replaceProtection(sheet) {
  const owned=sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET).filter(item=>
    ['Backup data managed by the private backup script.',LAYOUT_PROTECTION].includes(item.getDescription()));
  if (owned.length!==1) fail('VERIFY');
  owned[0].setDescription(LAYOUT_PROTECTION).setUnprotectedRanges([sheet.getRange('B3'),sheet.getRange('E3')]);
}
function styleBase(sheet, tab, layout, span) {
  sheet.setFrozenRows(layout.frozenRows);
  sheet.setHiddenGridlines(true);
  sheet.setTabColor(layout.tabColor==='GREEN'?'#16834B':'#E3AD22');
  sheet.hideColumns(layout.helperStartCol,tab.cols-layout.helperStartCol+1);
  layout.columnWidths.forEach((width,index)=>sheet.setColumnWidth(index+1,width));
  sheet.getRange(1,1,tab.rows,Math.min(span,tab.cols)).setFontFamily('Aptos').setVerticalAlignment('MIDDLE');
}
function clearOwnedLayout(sheet, presentation) {
  sheet.setConditionalFormatRules([]);
  sheet.getCharts().forEach(chart=>sheet.removeChart(chart));
  if (presentation==='recruit-profiles-v1') {
    sheet.getImages().filter(image=>image.getAnchorCell().getRow()===3 && image.getAnchorCell().getColumn()===10).forEach(image=>image.remove());
  }
}
function applyResultsLayout(sheet, tab, layout) {
  clearOwnedLayout(sheet,tab.presentation);
  styleBase(sheet,tab,layout,10);
  sheet.getRange('A1:J1').breakApart().merge().setBackground('#223449').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(18);
  sheet.getRange('A2:J2').breakApart().merge().setFontColor('#667085').setFontSize(10);
  ['B3','E3'].forEach(cell=>sheet.getRange(cell).setBackground('#EAF1F8').setFontWeight('bold').setFontColor('#223449'));
  sheet.getRange('A5:J5').setBackground('#223449').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true);
  setListValidation(sheet,'B3',layout.blocks.scopeOptions); setListValidation(sheet,'E3',layout.blocks.viewOptions);
  const start=layout.visibleStartRow, end=start+layout.visibleCapacity-1;
  applyFormulaRanges(sheet,resultsFormulaRanges(layout));
  for(let row=start;row<=end;row++) {
    sheet.getRange(row,1,1,10).setWrap(true);
    if ((row-start)%2) sheet.getRange(row,1,1,10).setBackground('#F7F9FC');
    sheet.setRowHeight(row,44);
  }
  sheet.getRange(start,4,layout.visibleCapacity,1).setNumberFormat('0.00');
  const rules=[
    textRule(sheet,`G${start}:G${end}`,'Complete','#E8F5ED','#16834B'),
    textRule(sheet,`G${start}:G${end}`,'Incomplete','#FFF5D9','#745300'),
    ...layout.bandStyles.map(style=>textRule(sheet,`H${start}:H${end}`,style.label,style.background,style.font)),
  ];
  sheet.setConditionalFormatRules(rules);
  replaceProtection(sheet);
}
function summaryFormula(layout, offset, blank) {
  const block=layout.blocks.summaries;
  return scalarLookup('$B$3&"|"&$E$3',blockRange(block,0),blockRange(block,offset),blank);
}
function profileFormulaRanges(layout) {
  const ranges=[];
  const summary=layout.blocks.summaries;
  const single=(row,col,formula)=>ranges.push({row,col,values:[[formula]]});
  single(3,8,summaryFormula(layout,1,''));
  [[5,2,4],[5,5,2],[5,8,3],[6,2,5],[6,5,6],[6,8,8],[7,8,16]].forEach(([row,col,offset])=>single(row,col,summaryFormula(layout,offset)));
  const attendance=scalarLookup('$B$3&"|"&$E$3',blockRange(summary,0),blockRange(summary,7),'');
  const comment=scalarLookup('$B$3&"|"&$E$3',blockRange(summary,0),blockRange(summary,9),'');
  single(7,2,`=IFERROR(${attendance.slice(1)}&IF(${comment.slice(1)}="",""," · "&${comment.slice(1)}),"—")`);
  const score=`VALUE(XLOOKUP($B$3&"|"&$E$3,${blockRange(summary,0)},${blockRange(summary,10)}))`;
  const overallRank=`XLOOKUP($B$3&"|"&$E$3,${blockRange(summary,0)},${blockRange(summary,12)})`;
  const overallPopulation=`XLOOKUP($B$3&"|"&$E$3,${blockRange(summary,0)},${blockRange(summary,13)})`;
  const journeyRank=`XLOOKUP($B$3&"|"&$E$3,${blockRange(summary,0)},${blockRange(summary,14)})`;
  const journeyPopulation=`XLOOKUP($B$3&"|"&$E$3,${blockRange(summary,0)},${blockRange(summary,15)})`;
  single(7,5,`=IFERROR(TEXT(${score},"0.00")&" · overall "&${overallRank}&"/"&${overallPopulation}&" · Journee "&${journeyRank}&"/"&${journeyPopulation},"—")`);
  const sections=layout.sectionRows;
  const dimension=layout.blocks.dimensions;
  const dimensionValues=[];
  for(let index=0;index<layout.dimensionCount;index++) {
    const row=sections.dimensionStart+index,key=`$B$3&"|"&$E$3&"|"&$A${row}`;
    dimensionValues.push([
      numericScalarLookup(key,`${blockRange(dimension,0)}&"|"&${blockRange(dimension,1)}`,blockRange(dimension,2)),
      scalarLookup(key,`${blockRange(dimension,0)}&"|"&${blockRange(dimension,1)}`,blockRange(dimension,3),''),
      scalarLookup(key,`${blockRange(dimension,0)}&"|"&${blockRange(dimension,1)}`,blockRange(dimension,4),''),
      scalarLookup(key,`${blockRange(dimension,0)}&"|"&${blockRange(dimension,1)}`,blockRange(dimension,5),''),
    ]);
  }
  ranges.push({row:sections.dimensionStart,col:2,values:dimensionValues});
  const activity=layout.blocks.activities;
  const activityValues=[];
  for(let index=0;index<layout.activityCount;index++) {
    const row=sections.activityStart+index,key=`$B$3&"|"&$E$3&"|"&$A${row}`;
    activityValues.push([
      numericScalarLookup(key,`${blockRange(activity,0)}&"|"&${blockRange(activity,1)}`,blockRange(activity,2)),
      scalarLookup(key,`${blockRange(activity,0)}&"|"&${blockRange(activity,1)}`,blockRange(activity,3),''),
      scalarLookup(key,`${blockRange(activity,0)}&"|"&${blockRange(activity,1)}`,blockRange(activity,4),''),
      scalarLookup(key,`${blockRange(activity,0)}&"|"&${blockRange(activity,1)}`,blockRange(activity,5),''),
    ]);
  }
  ranges.push({row:sections.activityStart,col:2,values:activityValues});
  const factorOffsets=[]; for(let index=0;index<layout.factorCount;index++) factorOffsets.push(18+index);
  const generalOffsets=[...factorOffsets,18+layout.factorCount,17,19+layout.factorCount,20+layout.factorCount];
  ranges.push({row:sections.generalStart,col:2,values:generalOffsets.map((offset,index)=>[
    index<=layout.factorCount
      ? numericScalarLookup('$B$3&"|"&$E$3',blockRange(summary,0),blockRange(summary,offset))
      : summaryFormula(layout,offset,''),
  ])});
  const applyFiltered=(block,start,capacity,columns)=>{
    const values=[];
    for(let index=0;index<capacity;index++) {
      const row=start+index,ordinal=`ROWS($A$${start}:$A${row})`;
      const formulas=[];
      for(let column=1;column<=columns;column++) formulas.push(indexedFilter('$H$3',blockRange(block,0),blockRange(block,column),ordinal));
      values.push(formulas);
    }
    ranges.push({row:start,col:1,values});
  };
  applyFiltered(layout.blocks.evaluators,sections.evaluatorStart,layout.evaluatorCapacity,6);
  applyFiltered(layout.blocks.criteria,sections.criterionStart,layout.criterionCapacity,8);
  applyFiltered(layout.blocks.audit,sections.auditStart,layout.auditCapacity,6);
  return ranges;
}
function applyProfileFormulas(sheet, layout) {
  applyFormulaRanges(sheet,profileFormulaRanges(layout));
}
function decodePreviewRows(rows) {
  rows.sort((a,b)=>Number(a[1])-Number(b[1]));
  if (!rows.length || rows.length!==Number(rows[0][2]) || rows.length>64 ||
      rows.some((row,index)=>Number(row[1])!==index||Number(row[2])!==rows.length||row[3]!==rows[0][3]||
        typeof row[4]!=='string'||!row[4].length||row[4].length>12000)) fail('VERIFY');
  const bytes=Utilities.base64Decode(rows.map(row=>row[4]).join(''));
  const signature=[137,80,78,71,13,10,26,10];
  if (!bytes.length || bytes.length>524288 || signature.some((value,index)=>((bytes[index]+256)%256)!==value) || hashBytes(bytes)!==rows[0][3]) fail('VERIFY');
  return bytes;
}
function selectedPreviewBytes_(sheet, layout, selected) {
  const options=blockValues_(sheet,layout.blocks.dependentOptions);
  const match=options.find(row=>String(row[0])===selected);
  if (!match || !match[1]) fail('VERIFY');
  const key=String(match[1]);
  return decodePreviewRows(blockValues_(sheet,layout.blocks.previews).filter(row=>String(row[0])===key));
}
function verifyPreviewStore_(sheet, layout) {
  const options=blockValues_(sheet,layout.blocks.dependentOptions);
  if(options.length!==layout.expectedPreviewCount) fail('VERIFY');
  const optionLabels=new Set(),optionKeys=new Set();
  options.forEach(row=>{
    const label=String(row[0]),key=String(row[1]);
    if(!label||!key||optionLabels.has(label)||optionKeys.has(key)) fail('VERIFY');
    optionLabels.add(label);optionKeys.add(key);
  });
  const profileKeys=new Set(blockValues_(sheet,layout.blocks.profileOptions).map(row=>String(row[2])).filter(Boolean));
  if(profileKeys.size!==layout.expectedPreviewCount || [...profileKeys].some(key=>!optionKeys.has(key))) fail('VERIFY');
  const groups=new Map();
  blockValues_(sheet,layout.blocks.previews).forEach(row=>{
    const key=String(row[0]);
    if(!optionKeys.has(key)) fail('VERIFY');
    if(!groups.has(key)) groups.set(key,[]);
    groups.get(key).push(row);
  });
  if(groups.size!==layout.expectedPreviewCount) fail('VERIFY');
  groups.forEach(rows=>decodePreviewRows(rows));
}
function refreshInitialProfileImage(sheet, layout) {
  if (!layout.expectedPreviewCount) return;
  const selected=String(sheet.getRange('E3').getValue());
  const bytes=selectedPreviewBytes_(sheet,layout,selected);
  sheet.getImages().filter(image=>image.getAnchorCell().getRow()===3&&image.getAnchorCell().getColumn()===10).forEach(image=>image.remove());
  const image=sheet.insertImage(Utilities.newBlob(bytes,'image/png','profile-preview.png'),10,3);
  if (image.setWidth) image.setWidth(150).setHeight(150);
}
function applyProfileLayout(sheet, tab, layout) {
  clearOwnedLayout(sheet,tab.presentation);
  sheet.getDeveloperMetadata().filter(item=>item.getKey()===PROFILE_LAYOUT_KEY).forEach(item=>item.remove());
  sheet.addDeveloperMetadata(PROFILE_LAYOUT_KEY,JSON.stringify({v:1,e:layout.expectedPreviewCount,
    o:[layout.blocks.profileOptions.startRow,layout.blocks.profileOptions.endRow,layout.blocks.profileOptions.startCol,layout.blocks.profileOptions.endCol],
    d:[layout.blocks.dependentOptions.startRow,layout.blocks.dependentOptions.endRow,layout.blocks.dependentOptions.startCol,layout.blocks.dependentOptions.endCol],
    p:[layout.blocks.previews.startRow,layout.blocks.previews.endRow,layout.blocks.previews.startCol,layout.blocks.previews.endCol]}));
  styleBase(sheet,tab,layout,12);
  sheet.getRange('A1:L1').breakApart().merge().setBackground('#223449').setFontColor('#FFFFFF').setFontWeight('bold').setFontSize(18);
  sheet.getRange('A2:L2').breakApart().merge().setFontColor('#667085').setFontSize(10);
  ['B3','E3'].forEach(cell=>sheet.getRange(cell).setBackground('#FFF5D9').setFontWeight('bold').setFontColor('#223449'));
  setListValidation(sheet,'B3',layout.blocks.scopeOptions); setListValidation(sheet,'E3',layout.blocks.dependentOptions);
  applyProfileFormulas(sheet,layout);
  const s=layout.sectionRows;
  [s.dimension,s.activity,s.general,s.evaluator,s.criterion,s.audit].forEach(row=>sheet.getRange(row,1,1,12).setBackground('#EAF1F8').setFontColor('#223449').setFontWeight('bold'));
  [s.dimensionHeader,s.activityHeader,s.evaluatorHeader,s.criterionHeader,s.auditHeader].forEach(row=>sheet.getRange(row,1,1,12).setBackground('#223449').setFontColor('#FFFFFF').setFontWeight('bold').setWrap(true));
  for(let index=0;index<layout.factorCount+4;index++) sheet.getRange(s.generalStart+index,2,1,11).breakApart().merge().setWrap(true);
  const rules=[
    ...layout.bandStyles.map(style=>textRule(sheet,'H7',style.label,style.background,style.font)),
    textRule(sheet,`D${s.dimensionStart}:D${s.dimensionStart+layout.dimensionCount-1}`,'Complete','#E8F5ED','#16834B'),
    textRule(sheet,`D${s.dimensionStart}:D${s.dimensionStart+layout.dimensionCount-1}`,'Incomplete','#FFF5D9','#745300'),
    textRule(sheet,`E${s.activityStart}:E${s.activityStart+layout.activityCount-1}`,'Complete','#E8F5ED','#16834B'),
    textRule(sheet,`E${s.activityStart}:E${s.activityStart+layout.activityCount-1}`,'Incomplete','#FFF5D9','#745300'),
    textRule(sheet,`E${s.evaluatorStart}:E${s.evaluatorStart+layout.evaluatorCapacity-1}`,'Complete','#E8F5ED','#16834B'),
    textRule(sheet,`E${s.evaluatorStart}:E${s.evaluatorStart+layout.evaluatorCapacity-1}`,'Missing','#FCE9ED','#C8102E'),
    textRule(sheet,`H${s.criterionStart}:H${s.criterionStart+layout.criterionCapacity-1}`,'Complete','#E8F5ED','#16834B'),
    textRule(sheet,`H${s.criterionStart}:H${s.criterionStart+layout.criterionCapacity-1}`,'Incomplete','#FFF5D9','#745300'),
  ];
  sheet.setConditionalFormatRules(rules);
  layout.charts.forEach(chart=>{
    const anchor=sheet.getRange(chart.anchor);
    const built=sheet.newChart().setChartType(Charts.ChartType.RADAR)
      .addRange(sheet.getRange(chart.startRow,chart.labelCol,chart.endRow-chart.startRow+1,2))
      .setPosition(anchor.getRow(),anchor.getColumn(),0,0).setOption('legend',{position:'none'})
      .setOption('vAxis',{viewWindow:{min:0,max:chart.maximum}}).build();
    sheet.insertChart(built);
  });
  refreshInitialProfileImage(sheet,layout);
  replaceProtection(sheet);
}
function applyPresentationLayout(op, state, ss) {
  const tab=state.tabs.find(item=>item.name===op.tab);
  if (!tab || !tab.presentation || tab.written!==tab.rows || tab.verified!==tab.rows) fail('INCOMPLETE');
  const layout=validateLayout(op,tab),sheet=stage(ss,state,tab.name);
  if (tab.presentation==='results-v1') applyResultsLayout(sheet,tab,layout);
  else applyProfileLayout(sheet,tab,layout);
  tab.layoutDigest=hash(JSON.stringify({presentation:op.presentation,layout}));
  tab.layoutVerified=false;
}
function verifySelector_(sheet, cell, block) {
  const values=blockValues_(sheet,block).map(row=>String(row[0]));
  const selected=String(sheet.getRange(cell).getValue()),rule=sheet.getRange(cell).getDataValidation();
  if(!values.length) {
    if(rule || selected) fail('VERIFY');
    return;
  }
  if(!rule || !values.includes(selected) || !rule.getCriteriaType ||
      rule.getCriteriaType()!==SpreadsheetApp.DataValidationCriteria.VALUE_IN_RANGE ||
      !rule.getCriteriaValues || !rule.getAllowInvalid || rule.getAllowInvalid()!==false) fail('VERIFY');
  const criteria=rule.getCriteriaValues(),expected=sheet.getRange(block.startRow+1,block.startCol,block.endRow-block.startRow,1).getA1Notation();
  if(!criteria.length || !criteria[0] || criteria[0].getA1Notation()!==expected) fail('VERIFY');
}
function verifyPresentationLayout(op, state, ss) {
  const tab=state.tabs.find(item=>item.name===op.tab);
  if (!tab || !tab.presentation) fail('TABS');
  const layout=validateLayout(op,tab),digest=hash(JSON.stringify({presentation:op.presentation,layout}));
  if (tab.layoutDigest!==digest) fail('VERIFY');
  const sheet=stage(ss,state,tab.name), expectedRules=(tab.presentation==='results-v1'?2:8)+layout.bandStyles.length;
  if (sheet.getFrozenRows()!==layout.frozenRows || !sheet.isColumnHiddenByUser(layout.helperStartCol) ||
      sheet.getConditionalFormatRules().length!==expectedRules) fail('VERIFY');
  if (tab.presentation==='results-v1') {
    verifySelector_(sheet,'B3',layout.blocks.scopeOptions);verifySelector_(sheet,'E3',layout.blocks.viewOptions);
    verifyFormulaRanges(sheet,resultsFormulaRanges(layout));
    if (sheet.getCharts().length!==0 || sheet.getImages().length!==0) fail('VERIFY');
  } else {
    verifySelector_(sheet,'B3',layout.blocks.scopeOptions);verifySelector_(sheet,'E3',layout.blocks.dependentOptions);
    verifyFormulaRanges(sheet,profileFormulaRanges(layout));
    verifyPreviewStore_(sheet,layout);
    const imageCount=sheet.getImages().filter(image=>image.getAnchorCell().getRow()===3&&image.getAnchorCell().getColumn()===10).length;
    const profileMetadata=sheet.getDeveloperMetadata().filter(item=>item.getKey()===PROFILE_LAYOUT_KEY);
    if (sheet.getCharts().length!==2 || imageCount!==(layout.expectedPreviewCount?1:0) || profileMetadata.length!==1) fail('VERIFY');
  }
  const owned=sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET).filter(item=>item.getDescription()===LAYOUT_PROTECTION);
  if (owned.length!==1 || JSON.stringify(owned[0].getUnprotectedRanges().map(range=>range.getA1Notation()).sort())!==JSON.stringify(['B3','E3'])) fail('VERIFY');
  tab.layoutVerified=true;
}
function applyOperation(op, state, ss) {
  if (!op || typeof op.kind !== 'string') fail('SEQUENCE');
  if (op.kind === 'batch') {
    if (!Array.isArray(op.operations) || !op.operations.length || op.operations.length>8 ||
        op.operations.some(item=>!item || item.kind==='batch' || item.kind==='prepare' || item.kind==='layout' || item.kind==='verifyLayout' || item.kind==='publish')) fail('SEQUENCE');
    // Each child is independently idempotent. If an invocation stops before
    // state persistence, replay overwrites the same rows and replaces images
    // at the same anchors before the sequence advances.
    op.operations.forEach(item=>applyOperation(item,state,ss));
    return;
  }
  if (op.kind === 'prepare') {
    if (state.tabs.length) fail('SEQUENCE');
    // A Google batch may have committed before state persistence failed. Reuse
    // only our exact deterministic staging allocation, rather than counting it twice.
    validateTabs(op.tabs, 0);
    const base = parseInt(state.runId.slice(0,7),16)*4;
    const reused = new Set();
    op.tabs.forEach((t,index) => {
      const sheet = ss.getSheetById(base+index);
      if (!sheet) return;
      if (!meta(sheet,OWNED_KEY) || meta(sheet,OWNED_KEY).getValue() !== state.runId ||
          sheet.getName() !== stageTitle(state,t.name) || sheet.getMaxRows() !== t.rows || sheet.getMaxColumns() !== t.cols) fail('COLLISION');
      reused.add(base+index);
    });
    validateTabs(op.tabs, ss.getSheets().reduce((n,s) => n+(reused.has(s.getSheetId()) ? 0 : s.getMaxRows()*s.getMaxColumns()),0));
    const current = published(ss);
    op.tabs.forEach(t => {
      const collision = ss.getSheetByName(finalTitle(t));
      if (collision && (!meta(collision,OWNED_KEY) || meta(collision,OWNED_KEY).getValue() !== (current || {}).runId)) fail('COLLISION');
    });
    const existingIds = new Set(ss.getSheets().map(s=>s.getSheetId()));
    // Deterministic IDs make a retried prepare safe after a lost response.
    const requests = [];
    state.tabs = op.tabs.map((t,index) => {
      const id = base+index;
      if (existingIds.has(id)) {
        const sheet = ss.getSheetById(id);
        if (!meta(sheet,OWNED_KEY) || meta(sheet,OWNED_KEY).getValue() !== state.runId) fail('COLLISION');
      } else {
        requests.push({addSheet:{properties:{sheetId:id,title:stageTitle(state,t.name),hidden:true,
          gridProperties:{rowCount:t.rows,columnCount:t.cols,frozenRowCount:t.rows>1 ? 1 : 0}}}});
        requests.push({createDeveloperMetadata:{developerMetadata:{metadataKey:OWNED_KEY,metadataValue:state.runId,
          location:{sheetId:id},visibility:'DOCUMENT'}}});
        requests.push({addProtectedRange:{protectedRange:{range:{sheetId:id},description:'Backup data managed by the private backup script.',warningOnly:false,
          editors:{users:[],groups:[],domainUsersCanEdit:false}}}});
      }
      const stored={name:t.name,rows:t.rows,cols:t.cols,hidden:!!t.hidden,id,written:0,verified:0};
      if (t.presentation) Object.assign(stored,{presentation:t.presentation,layoutDigest:'',layoutVerified:false});
      return stored;
    });
    state.snapshotAt = op.manifest.snapshotAt;
    state.workspaceName = op.manifest.workspaceName;
    state.photoCount = op.manifest.photoCount;
    state.recordsSha256 = op.manifest.recordsSha256;
    state.expectedRecordChain = op.manifest.recordChain;
    state.expectedRecords = op.manifest.recordCount;
    state.recordChain = '';
    state.verifiedRecords = 0;
    state.verifiedRecordRows = 0;
    if (requests.length) Sheets.Spreadsheets.batchUpdate({requests}, BACKUP_SHEET);
    return;
  }
  if (!state.tabs.length) fail('INCOMPLETE');
  if (op.kind === 'rows') {
    const t = state.tabs.find(t=>t.name===op.tab);
    if (!t || op.start !== t.written+1 || !Array.isArray(op.rows) || !op.rows.length || op.start+op.rows.length-1>t.rows ||
        op.rows.some(r=>r.length!==t.cols || r.some(c=>typeof c!=='string' || c.length>49000))) fail('SEQUENCE');
    const sheet = stage(ss,state,op.tab);
    Sheets.Spreadsheets.Values.update({values:op.rows}, BACKUP_SHEET, `'${sheet.getName()}'!A${op.start}`, {valueInputOption:'RAW'});
    verifyRows(op.rows,sheet.getRange(op.start,1,op.rows.length,t.cols).getValues());
    t.written += op.rows.length;
    return;
  }
  if (op.kind === 'verifyCells') {
    const t = state.tabs.find(t=>t.name===op.tab);
    if (!t || t.written!==t.rows || op.start!==t.verified+1 || !Number.isInteger(op.count) ||
        op.count<1 || op.start+op.count-1>t.rows) fail('SEQUENCE');
    const rows = stage(ss,state,op.tab).getRange(op.start,1,op.count,t.cols).getValues().map(r=>r.map(String));
    if (hash(JSON.stringify(rows))!==op.sha256) fail('VERIFY');
    t.verified += op.count;
    return;
  }
  if (op.kind === 'verifyRecords') {
    const t = state.tabs.find(t=>t.name==='_Records');
    if (!t || t.verified!==t.rows || op.start!==state.verifiedRecordRows+2 ||
        !Array.isArray(op.records) || !op.records.length || op.records.length>200) fail('SEQUENCE');
    let totalRows = 0;
    op.records.forEach(item => {
      const n = Number(item[2]);
      if (!Number.isInteger(n) || n<1) fail('VERIFY');
      totalRows += n;
    });
    if (totalRows>1000 || op.start+totalRows-1>t.rows) fail('VERIFY');
    const sheet = stage(ss,state,'_Records');
    const allRows = sheet.getRange(op.start,1,totalRows,6).getValues();
    let offset = 0;
    op.records.forEach(item => {
      const [table,index,count,digest] = item;
      const n = Number(count);
      const rows = allRows.slice(offset,offset+n);
      if (rows.some((r,i)=>r[0]!==table || String(r[1])!==index || String(r[2])!==String(i) ||
          String(r[3])!==count || r[4]!==digest)) fail('VERIFY');
      const content = rows.map(r=>r[5]).join('');
      if (hash(content)!==digest) fail('VERIFY');
      try { JSON.parse(content); } catch (_) { fail('VERIFY'); }
      state.recordChain = hash(state.recordChain+hash(JSON.stringify(item)));
      state.verifiedRecords++;
      state.verifiedRecordRows += n;
      offset += n;
    });
    return;
  }
  if (op.kind === 'image') {
    if (op.row !== state.images+2) fail('SEQUENCE');
    const bytes = Utilities.base64Decode(op.data);
    if (bytes.length>2000000 || hashBytes(bytes)!==op.sha256) fail('VERIFY');
    const sheet = stage(ss,state,'Photos');
    // Retry after a lost response: replace only this staged row's image.
    sheet.getImages().filter(i=>i.getAnchorCell().getRow()===op.row).forEach(i=>i.remove());
    sheet.insertImage(Utilities.newBlob(bytes,'image/png','preview.png'),1,op.row);
    sheet.setRowHeight(op.row,170); sheet.setColumnWidth(1,180);
    state.images++;
    return;
  }
  if (op.kind === 'verifyPhoto') {
    const rows = stage(ss,state,'_Photo bytes').getDataRange().getValues().slice(1).filter(r=>r[0]===op.recruitId);
    if (!rows.length || rows.length!==Number(rows[0][2]) || rows.some((r,i)=>Number(r[1])!==i || r[3]!==op.sha256)) fail('VERIFY');
    const bytes = Utilities.base64Decode(rows.map(r=>r[5]).join(''));
    if (hashBytes(bytes)!==op.sha256) fail('VERIFY');
    state.verifiedPhotos++;
    return;
  }
  if (op.kind === 'layout') {
    applyPresentationLayout(op,state,ss);
    return;
  }
  if (op.kind === 'verifyLayout') {
    verifyPresentationLayout(op,state,ss);
    return;
  }
  if (op.kind !== 'publish') fail('SEQUENCE');
  const records = state.tabs.find(t=>t.name==='_Records');
  if (state.tabs.some(t=>t.written!==t.rows || t.verified!==t.rows || (t.presentation && !t.layoutVerified)) || state.images!==state.photoCount || state.verifiedPhotos!==state.photoCount ||
      !records || state.verifiedRecordRows!==records.rows-1 || state.verifiedRecords!==state.expectedRecords ||
      state.recordChain!==state.expectedRecordChain) fail('INCOMPLETE');
  const current = published(ss);
  const requests = [];
  // Keep a visible sheet throughout even when the user removed the original Sheet1.
  const visible = state.tabs.find(t=>!t.hidden);
  if (!visible) fail('TABS');
  requests.push({updateSheetProperties:{properties:{sheetId:visible.id,hidden:false},fields:'hidden'}});
  // Deleting the old owned version and exposing the new one are ONE atomic batch.
  ss.getSheets().forEach(sheet => {
    const tag = meta(sheet,OWNED_KEY);
    if (tag && tag.getValue()===(current || {}).runId) requests.push({deleteSheet:{sheetId:sheet.getSheetId()}});
  });
  state.tabs.forEach((t,index)=>{
    stage(ss,state,t.name);
    const targetTitle=t.presentation?PRESENTATIONS[t.presentation].finalTitle:`Backup - ${t.name}`;
    const collision = ss.getSheetByName(targetTitle);
    if (collision && (!meta(collision,OWNED_KEY) || meta(collision,OWNED_KEY).getValue() !== (current || {}).runId)) fail('COLLISION');
    requests.push({updateSheetProperties:{properties:{sheetId:t.id,title:targetTitle,hidden:t.hidden,index},fields:'title,hidden,index'}});
    if (t.presentation) return;
    requests.push({repeatCell:{range:{sheetId:t.id,startRowIndex:0,endRowIndex:1},cell:{userEnteredFormat:{
      backgroundColor:{red:0.10,green:0.13,blue:0.18},textFormat:{bold:true,foregroundColor:{red:1,green:1,blue:1}},wrapStrategy:'WRAP'}},fields:'userEnteredFormat'}});
    requests.push({updateDimensionProperties:{range:{sheetId:t.id,dimension:'COLUMNS',startIndex:0,endIndex:t.cols},properties:{pixelSize:180},fields:'pixelSize'}});
    requests.push({updateDimensionProperties:{range:{sheetId:t.id,dimension:'ROWS',startIndex:0,endIndex:1},properties:{pixelSize:44},fields:'pixelSize'}});
    if (t.rows>1 && !t.hidden) requests.push({setBasicFilter:{filter:{range:{sheetId:t.id,startRowIndex:0,endRowIndex:t.rows,startColumnIndex:0,endColumnIndex:t.cols}}}});
  });
  ss.getDeveloperMetadata().filter(m=>m.getKey()===PUBLISHED_KEY).forEach(m=>requests.push({deleteDeveloperMetadata:{dataFilter:{developerMetadataLookup:{metadataId:m.getId()}}}}));
  const marker = {runId:state.runId,snapshotAt:state.snapshotAt,completedAt:new Date().toISOString(),workspaceName:state.workspaceName,recordsSha256:state.recordsSha256,photos:state.photoCount};
  requests.push({createDeveloperMetadata:{developerMetadata:{metadataKey:PUBLISHED_KEY,metadataValue:JSON.stringify(marker),location:{spreadsheet:true},visibility:'DOCUMENT'}}});
  Sheets.Spreadsheets.batchUpdate({requests},BACKUP_SHEET);
}

function profileTriggerLayout_(sheet) {
  const entries=sheet.getDeveloperMetadata().filter(item=>item.getKey()===PROFILE_LAYOUT_KEY);
  if (entries.length!==1) fail('VERIFY');
  let value;
  try { value=JSON.parse(entries[0].getValue()); } catch (_) { fail('VERIFY'); }
  if (!value || value.v!==1 || !Number.isInteger(value.e) || value.e<0 || value.e>5000) fail('VERIFY');
  const parse=(input,width)=>{
    if (!Array.isArray(input)||input.length!==4||input.some(item=>!Number.isInteger(item))) fail('VERIFY');
    const [startRow,endRow,startCol,endCol]=input;
    if(startRow!==1||endRow<startRow||endRow>sheet.getMaxRows()||startCol<1||endCol>sheet.getMaxColumns()||endCol-startCol+1!==width) fail('VERIFY');
    return {startRow,endRow,startCol,endCol};
  };
  return {expected:value.e,profileOptions:parse(value.o,3),dependentOptions:parse(value.d,2),previews:parse(value.p,5)};
}
function blockValues_(sheet, block) {
  if (block.endRow<=block.startRow) return [];
  return sheet.getRange(block.startRow+1,block.startCol,block.endRow-block.startRow,block.endCol-block.startCol+1).getValues();
}
function refreshDependentProfileOptions_(sheet, layout) {
  const scope=String(sheet.getRange('B3').getValue());
  const options=blockValues_(sheet,layout.profileOptions).filter(row=>String(row[0])===scope).map(row=>[String(row[1]),String(row[2])]);
  const capacity=layout.dependentOptions.endRow-layout.dependentOptions.startRow;
  if (options.length>capacity) fail('VERIFY');
  if (capacity) sheet.getRange(layout.dependentOptions.startRow+1,layout.dependentOptions.startCol,capacity,2).clearContent();
  if (options.length) sheet.getRange(layout.dependentOptions.startRow+1,layout.dependentOptions.startCol,options.length,2).setValues(options);
  const selector=sheet.getRange('E3');
  selector.clearDataValidations();
  if (options.length) {
    const source=sheet.getRange(layout.dependentOptions.startRow+1,layout.dependentOptions.startCol,options.length,1);
    selector.setDataValidation(SpreadsheetApp.newDataValidation().requireValueInRange(source,true).setAllowInvalid(false)
      .setHelpText('Choose a recruit from the selected completed Journee.').build());
  }
  selector.setValue(options.length?options[0][0]:'');
}
function refreshProfilePhoto_(spreadsheet, profileSheet) {
  if (!spreadsheet || spreadsheet.getId()!==BACKUP_SHEET || !profileSheet || profileSheet.getName()!=='Recruit Profiles') return;
  const complete=published(spreadsheet),owner=meta(profileSheet,OWNED_KEY);
  if (!complete || !owner || owner.getValue()!==complete.runId) return;
  const layout=profileTriggerLayout_(profileSheet),selected=String(profileSheet.getRange('E3').getValue());
  if (!selected) {
    profileSheet.getImages().filter(image=>image.getAnchorCell().getRow()===3&&image.getAnchorCell().getColumn()===10).forEach(image=>image.remove());
    return;
  }
  const bytes=selectedPreviewBytes_(profileSheet,{blocks:{dependentOptions:layout.dependentOptions,previews:layout.previews}},selected);
  // Validate fully before changing the last good image.
  profileSheet.getImages().filter(image=>image.getAnchorCell().getRow()===3&&image.getAnchorCell().getColumn()===10).forEach(image=>image.remove());
  const image=profileSheet.insertImage(Utilities.newBlob(bytes,'image/png','profile-preview.png'),10,3);
  if (image.setWidth) image.setWidth(150).setHeight(150);
}
function profileSelectionChanged(event) {
  if (!event || !event.source || event.source.getId()!==BACKUP_SHEET || !event.range ||
      event.range.getNumRows()!==1 || event.range.getNumColumns()!==1) return;
  const sheet=event.range.getSheet(),cell=event.range.getA1Notation();
  if (!sheet || sheet.getName()!=='Recruit Profiles' || !['B3','E3'].includes(cell)) return;
  const complete=published(event.source),owner=meta(sheet,OWNED_KEY);
  if (!complete || !owner || owner.getValue()!==complete.runId) return;
  const layout=profileTriggerLayout_(sheet);
  if (cell==='B3') refreshDependentProfileOptions_(sheet,layout);
  SpreadsheetApp.flush();
  refreshProfilePhoto_(event.source,sheet);
}
function installInteractiveProfileTrigger() {
  ScriptApp.getProjectTriggers().filter(trigger=>trigger.getHandlerFunction()==='profileSelectionChanged' &&
    trigger.getTriggerSourceId()===BACKUP_SHEET).forEach(trigger=>ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger('profileSelectionChanged').forSpreadsheet(BACKUP_SHEET).onEdit().create();
}

// Runs once in the owner account to show Google's consent screen, verify access,
// and install exactly one private selector trigger.
function authorizeBackup() {
  SpreadsheetApp.openById(BACKUP_SHEET).getName();
  Sheets.Spreadsheets.get(BACKUP_SHEET,{fields:'spreadsheetId'});
  installInteractiveProfileTrigger();
}

if (typeof module !== 'undefined') module.exports = {validateEnvelope,validateDestination,checkLease,checkSequence,validateTabs,finalTitle,verifyRows,dispatch,applyOperation,applyPresentationLayout,verifyPresentationLayout,profileSelectionChanged,refreshProfilePhoto_,installInteractiveProfileTrigger};
