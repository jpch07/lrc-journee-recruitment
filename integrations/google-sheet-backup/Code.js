/* Private STANDALONE Apps Script. Never bind this to a publicly editable sheet.
 * Script properties: BACKUP_SECRET (32+ characters), WORKSPACE_ID.
 * Enable the Sheets v4 advanced service. No Drive files/folders are created.
 */
const BACKUP_SHEET = '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0';
const LEASE_SECONDS = 900;
const OWNED_KEY = 'evalday_backup_run';
const PUBLISHED_KEY = 'evalday_backup_published';

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
function validateTabs(tabs, existingCells) {
  if (!Array.isArray(tabs) || !tabs.length || tabs.length > 60) fail('TABS');
  const names = new Set();
  let cells = 0;
  tabs.forEach(t => {
    if (typeof t.name !== 'string' || !/^[A-Za-z0-9 _-]{1,60}$/.test(t.name) || names.has(t.name) ||
        !Number.isInteger(t.rows) || t.rows < 1 || !Number.isInteger(t.cols) || t.cols < 1 || t.cols > 60) fail('TABS');
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
function applyOperation(op, state, ss) {
  if (!op || typeof op.kind !== 'string') fail('SEQUENCE');
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
      const collision = ss.getSheetByName(`Backup - ${t.name}`);
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
      return {name:t.name,rows:t.rows,cols:t.cols,hidden:!!t.hidden,id,written:0,verified:0};
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
        !Array.isArray(op.records) || !op.records.length || op.records.length>40) fail('SEQUENCE');
    let cursor = op.start;
    const sheet = stage(ss,state,'_Records');
    op.records.forEach(item => {
      const [table,index,count,digest] = item;
      const n = Number(count);
      if (!Number.isInteger(n) || n<1 || cursor+n-1>t.rows) fail('VERIFY');
      const rows = sheet.getRange(cursor,1,n,6).getValues();
      if (rows.some((r,i)=>r[0]!==table || String(r[1])!==index || String(r[2])!==String(i) ||
          String(r[3])!==count || r[4]!==digest)) fail('VERIFY');
      const content = rows.map(r=>r[5]).join('');
      if (hash(content)!==digest) fail('VERIFY');
      try { JSON.parse(content); } catch (_) { fail('VERIFY'); }
      state.recordChain = hash(state.recordChain+hash(JSON.stringify(item)));
      state.verifiedRecords++;
      state.verifiedRecordRows += n;
      cursor += n;
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
  if (op.kind !== 'publish') fail('SEQUENCE');
  const records = state.tabs.find(t=>t.name==='_Records');
  if (state.tabs.some(t=>t.written!==t.rows || t.verified!==t.rows) || state.images!==state.photoCount || state.verifiedPhotos!==state.photoCount ||
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
    const collision = ss.getSheetByName(`Backup - ${t.name}`);
    if (collision && (!meta(collision,OWNED_KEY) || meta(collision,OWNED_KEY).getValue() !== (current || {}).runId)) fail('COLLISION');
    requests.push({updateSheetProperties:{properties:{sheetId:t.id,title:`Backup - ${t.name}`,hidden:t.hidden,index},fields:'title,hidden,index'}});
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

// Runs once in the owner account to show Google's consent screen and verify access.
function authorizeBackup() {
  SpreadsheetApp.openById(BACKUP_SHEET).getName();
  Sheets.Spreadsheets.get(BACKUP_SHEET,{fields:'spreadsheetId'});
}

if (typeof module !== 'undefined') module.exports = {validateEnvelope,validateDestination,checkLease,checkSequence,validateTabs,verifyRows,dispatch,applyOperation};
