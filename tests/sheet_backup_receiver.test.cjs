const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const receiver = require('../integrations/google-sheet-backup/Code.js');

test('auth rejects unsigned, expired and wrong-destination requests', () => {
  assert.throws(() => receiver.validateEnvelope({}, 'x'.repeat(40), 1000, () => ''), /AUTH/);
  const payload = Buffer.from(JSON.stringify({action:'status', spreadsheetId:'wrong'})).toString('base64');
  const envelope = {timestamp:1000, nonce:'a'.repeat(32), payload};
  const sign = value => crypto.createHmac('sha256','x'.repeat(40)).update(value).digest('hex');
  envelope.signature = sign(`1000.${envelope.nonce}.${payload}`);
  assert.throws(() => receiver.validateEnvelope(envelope, 'x'.repeat(40), 2000, sign), /AUTH/);
  assert.equal(receiver.validateEnvelope(envelope, 'x'.repeat(40), 1000, sign), payload);
  assert.throws(() => receiver.validateDestination('wrong'), /DESTINATION/);
});

test('lease and sequence guards fence old jobs and make retries idempotent', () => {
  const active = {runId:'one', expires:2000, next:2, lastDigest:'abc'};
  assert.throws(() => receiver.checkLease(active, 'two', 1000), /BUSY/);
  assert.throws(() => receiver.checkLease(active, 'one', 3000), /EXPIRED/);
  assert.equal(receiver.checkSequence(active, 1, 'abc'), 'retry');
  assert.throws(() => receiver.checkSequence(active, 1, 'changed'), /SEQUENCE/);
  assert.equal(receiver.checkSequence(active, 2, 'next'), 'next');
});

test('preflight retains old data and rejects oversize or ambiguous tabs', () => {
  assert.throws(() => receiver.validateTabs([{name:'One',rows:5e6,cols:5}], 10), /CAPACITY/);
  assert.throws(() => receiver.validateTabs([{name:'One',rows:1,cols:1},{name:'One',rows:1,cols:1}], 0), /TABS/);
  assert.equal(receiver.validateTabs([{name:'One',rows:2,cols:3}], 10), 6);
});

test('literal data verifies without evaluating formulas or discarding blank cells', () => {
  receiver.verifyRows([['=IMPORTXML("bad")', '', 'ع😀']], [['=IMPORTXML("bad")', '', 'ع😀']]);
  assert.throws(() => receiver.verifyRows([['kept']], [['changed']]), /VERIFY/);
});

function fakeGoogle() {
  const sheets = new Map();
  let documentMetadata = [];
  let failPublish = false;
  const tag = (key,value,id=1) => ({getKey:()=>key,getValue:()=>value,getId:()=>id});
  const add = (id,title,rows=1,cols=2,owner=null) => {
    const s = {id,title,rows,cols,data:[],metadata:owner?[tag('evalday_backup_run',owner)]:[],hidden:false,
      getSheetId(){return this.id}, getName(){return this.title}, getMaxRows(){return this.rows}, getMaxColumns(){return this.cols},
      getDeveloperMetadata(){return this.metadata},
      getRange(start,col,n,width){return {getValues:()=>Array.from({length:n},(_,i)=>Array.from({length:width},(_,j)=>this.data[start+i-1]?.[col+j-1]??''))}},
      getDataRange(){return {getValues:()=>this.data}}, getImages(){return []}};
    sheets.set(id,s); return s;
  };
  add(1,'My own notes');
  const ss = {getSheets:()=>[...sheets.values()], getSheetById:id=>sheets.get(id),
    getSheetByName:name=>[...sheets.values()].find(s=>s.title===name), getDeveloperMetadata:()=>documentMetadata};
  global.SpreadsheetApp = {openById:()=>ss};
  global.Utilities = {Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},
    computeDigest:(_,value)=>[...crypto.createHash('sha256').update(value).digest()],
    newBlob:value=>({getBytes:()=>[...Buffer.from(value)]})};
  global.Sheets = {Spreadsheets:{
    Values:{update:(body,id,range,options)=>{
      assert.equal(options.valueInputOption,'RAW');
      const [,name,row] = range.match(/^'(.+)'!A(\d+)$/);
      const s=ss.getSheetByName(name);
      body.values.forEach((r,i)=>s.data[Number(row)-1+i]=r);
    }},
    batchUpdate:({requests})=>{
      if(failPublish && requests.some(r=>r.createDeveloperMetadata?.developerMetadata?.metadataKey==='evalday_backup_published')) throw new Error('provider failure');
      requests.forEach(r=>{
        if(r.addSheet) {const p=r.addSheet.properties;add(p.sheetId,p.title,p.gridProperties.rowCount,p.gridProperties.columnCount);}
        if(r.createDeveloperMetadata) {const d=r.createDeveloperMetadata.developerMetadata;const value=tag(d.metadataKey,d.metadataValue);if(d.location.sheetId!==undefined)sheets.get(d.location.sheetId).metadata.push(value);else documentMetadata.push(value);}
        if(r.deleteDeveloperMetadata) documentMetadata=[];
        if(r.deleteSheet) sheets.delete(r.deleteSheet.sheetId);
        if(r.updateSheetProperties) {const p=r.updateSheetProperties.properties;const s=sheets.get(p.sheetId);if(p.title)s.title=p.title;if(p.hidden!==undefined)s.hidden=p.hidden;}
      });
    }
  }};
  const values = {};
  const props = {getProperty:key=>values[key]||null,setProperty:(key,value)=>{values[key]=value},deleteProperty:key=>{delete values[key]}};
  return {ss,props,failPublish:value=>{failPublish=value}};
}

test('complete runs atomically replace only owned tabs; failures retain previous backup', () => {
  const env = fakeGoogle();
  const prepare = {kind:'prepare',tabs:[{name:'Example',rows:2,cols:2}],
    manifest:{photoCount:0,snapshotAt:'2026-10-04T10:00:00Z',workspaceName:'Example',recordsSha256:'abc'}};
  const run1='1'.repeat(32),run2='2'.repeat(32);
  const send=(runId,sequence,operation)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
  receiver.dispatch({action:'begin',runId:run1},env.props,1000);
  send(run1,0,prepare);
  assert.throws(()=>send(run1,1,{kind:'publish'}),/INCOMPLETE/);
  send(run1,1,{kind:'rows',tab:'Example',start:1,rows:[['Name','Note'],['=not a formula','Original']]});
  assert.equal(send(run1,1,{kind:'rows',tab:'Example',start:1,rows:[['Name','Note'],['=not a formula','Original']]}).next,2);
  assert.equal(send(run1,2,{kind:'publish'}).state,'complete');
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
  assert.equal(send(run1,2,{kind:'publish'}).state,'complete');
  receiver.dispatch({action:'begin',runId:run2},env.props,1000);
  send(run2,0,prepare);
  send(run2,1,{kind:'rows',tab:'Example',start:1,rows:[['Name','Note'],['New','Changed']]});
  env.failPublish(true);
  assert.throws(()=>send(run2,2,{kind:'publish'}),/provider failure/);
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
  env.failPublish(false);
  send(run2,2,{kind:'publish'});
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Changed');
  assert.ok(env.ss.getSheetByName('My own notes'));
  assert.equal(env.ss.getSheets().length,2);
});

test('a public user tab with a reserved title is never overwritten', () => {
  const env=fakeGoogle();
  env.ss.getSheetById(1).title='Backup - Example';
  const runId='3'.repeat(32);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  assert.throws(()=>receiver.dispatch({action:'apply',runId,sequence:0,operation:{kind:'prepare',tabs:[{name:'Example',rows:1,cols:1}],manifest:{}}},env.props,1000),/COLLISION/);
  assert.equal(env.ss.getSheets().length,1);
});
