const {test} = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
// Optionally exercise the regressions against a historical receiver, read-only.
const baseline = process.env.SHEET_BACKUP_REVIEW_BASELINE;
const receiver = baseline ? new Function('module', `${require('node:child_process').execFileSync('git',
  ['show',`${baseline}:integrations/google-sheet-backup/Code.js`],{encoding:'utf8'})}; return module.exports;`)({exports:{}})
  : require('../integrations/google-sheet-backup/Code.js');

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

test('batched operations replay safely after state persistence is lost', () => {
  const env=fakeGoogle(),runId='a'.repeat(32),ops=operations();
  const apply=(sequence,operation)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  apply(0,ops[0]);
  const beforeBatch=env.props.getProperty('RUN');
  const batch={kind:'batch',operations:ops.slice(1,-1)};
  assert.equal(apply(1,batch).next,2);
  env.props.setProperty('RUN',beforeBatch);
  assert.equal(apply(1,batch).next,2);
  assert.throws(()=>apply(2,{kind:'batch',operations:[{kind:'publish'}]}),/SEQUENCE/);
  assert.equal(apply(2,ops.at(-1)).state,'complete');
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
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
    const s = {id,title,rows,cols,data:[],metadata:owner?[tag('evalday_backup_run',owner)]:[],hidden:false,images:[],
      getSheetId(){return this.id}, getName(){return this.title}, getMaxRows(){return this.rows}, getMaxColumns(){return this.cols},
      getDeveloperMetadata(){return this.metadata},
      getRange(start,col,n,width){return {getValues:()=>Array.from({length:n},(_,i)=>Array.from({length:width},(_,j)=>this.data[start+i-1]?.[col+j-1]??''))}},
      getDataRange(){return {getValues:()=>this.data}}, getImages(){return this.images},
      insertImage(blob,col,row){const image={getAnchorCell:()=>({getRow:()=>row}),remove:()=>{this.images=this.images.filter(i=>i!==image)}};this.images.push(image)},
      setRowHeight(){},setColumnWidth(){}};
    sheets.set(id,s); return s;
  };
  add(1,'My own notes');
  const ss = {getSheets:()=>[...sheets.values()], getSheetById:id=>sheets.get(id),
    getSheetByName:name=>[...sheets.values()].find(s=>s.title===name), getDeveloperMetadata:()=>documentMetadata};
  global.SpreadsheetApp = {openById:()=>ss};
  global.Utilities = {Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},
    computeDigest:(_,value)=>[...crypto.createHash('sha256').update(Array.isArray(value)?Buffer.from(value):value).digest()],
    base64Decode:value=>[...Buffer.from(value,'base64')],
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
        if(r.addSheet) {const p=r.addSheet.properties;add(p.sheetId,p.title,p.gridProperties.rowCount,p.gridProperties.columnCount).hidden=p.hidden;}
        if(r.createDeveloperMetadata) {const d=r.createDeveloperMetadata.developerMetadata;const value=tag(d.metadataKey,d.metadataValue);if(d.location.sheetId!==undefined)sheets.get(d.location.sheetId).metadata.push(value);else documentMetadata.push(value);}
        if(r.deleteDeveloperMetadata) documentMetadata=[];
        if(r.deleteSheet) {
          const target=sheets.get(r.deleteSheet.sheetId);
          if(!target.hidden && ![...sheets.values()].some(s=>s.id!==target.id && !s.hidden)) throw new Error('last visible sheet');
          sheets.delete(r.deleteSheet.sheetId);
        }
        if(r.updateSheetProperties) {const p=r.updateSheetProperties.properties;const s=sheets.get(p.sheetId);if(p.title)s.title=p.title;if(p.hidden!==undefined)s.hidden=p.hidden;}
      });
    }
  }};
  const values = {};
  const props = {getProperty:key=>values[key]||null,setProperty:(key,value)=>{values[key]=value},deleteProperty:key=>{delete values[key]}};
  return {ss,props,failPublish:value=>{failPublish=value},remove:id=>sheets.delete(id)};
}

const digest = value=>crypto.createHash('sha256').update(value).digest('hex');
function operations(note='Original') {
  const content=JSON.stringify({note}), checksum=digest(content);
  const record=['example','0','1',checksum];
  const tabs=[{name:'Example',rows:[['Name','Note'],['=not a formula',note]]},
    {name:'_Records',hidden:true,rows:[['Table','Record','Part','Parts','SHA-256','JSON chunk'],['example','0','0','1',checksum,content]]}];
  return [{kind:'prepare',tabs:tabs.map(t=>({name:t.name,rows:t.rows.length,cols:t.rows[0].length,hidden:!!t.hidden})),
    manifest:{photoCount:0,snapshotAt:'2026-10-04T10:00:00Z',workspaceName:'Example',recordsSha256:'abc',
      recordChain:digest(digest(JSON.stringify(record))),recordCount:1}},
    ...tabs.map(t=>({kind:'rows',tab:t.name,start:1,rows:t.rows})),
    ...tabs.map(t=>({kind:'verifyCells',tab:t.name,start:1,count:t.rows.length,sha256:digest(JSON.stringify(t.rows))})),
    {kind:'verifyRecords',start:2,records:[record]},{kind:'publish'}];
}

test('complete runs atomically replace only owned tabs; failures retain previous backup', () => {
  const env = fakeGoogle();
  const run1='1'.repeat(32),run2='2'.repeat(32);
  const send=(runId,sequence,operation)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
  receiver.dispatch({action:'begin',runId:run1},env.props,1000);
  const first=operations();
  send(run1,0,first[0]);
  assert.throws(()=>send(run1,1,{kind:'publish'}),/INCOMPLETE/);
  first.slice(1).forEach((op,i)=>send(run1,i+1,op));
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
  assert.equal(send(run1,first.length-1,{kind:'publish'}).state,'complete');
  receiver.dispatch({action:'begin',runId:run2},env.props,1000);
  const second=operations('Changed');
  second.slice(0,-1).forEach((op,i)=>send(run2,i,op));
  env.failPublish(true);
  assert.throws(()=>send(run2,second.length-1,{kind:'publish'}),/provider failure/);
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
  env.failPublish(false);
  send(run2,second.length-1,{kind:'publish'});
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Changed');
  assert.ok(env.ss.getSheetByName('My own notes'));
  assert.equal(env.ss.getSheets().length,3);
});

test('changed acknowledged data fails final verification and cannot replace the old backup', () => {
  const env=fakeGoogle(), old='4'.repeat(32), runId='5'.repeat(32);
  const apply=(id,i,operation)=>receiver.dispatch({action:'apply',runId:id,sequence:i,operation},env.props,1000);
  receiver.dispatch({action:'begin',runId:old},env.props,1000);
  operations().forEach((op,i)=>apply(old,i,op));
  receiver.dispatch({action:'begin',runId},env.props,1000);
  const ops=operations('Changed');
  ops.slice(0,3).forEach((op,i)=>apply(runId,i,op));
  const staged=env.ss.getSheetByName(`_stage_${runId.slice(0,12)}__Records`);
  staged.data[1][5]='{"note":"Tampered"}';
  apply(runId,3,ops[3]);
  assert.throws(()=>apply(runId,4,ops[4]),/VERIFY/);
  assert.throws(()=>apply(runId,4,{kind:'publish'}),/INCOMPLETE/);
  assert.equal(env.ss.getSheetByName('Backup - Example').data[1][1],'Original');
});

test('fully uploaded but unverified records cannot be published', () => {
  const env=fakeGoogle(),runId='9'.repeat(32);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  operations().slice(0,3).forEach((operation,sequence)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000));
  const staged=env.ss.getSheetByName(`_stage_${runId.slice(0,12)}__Records`);
  staged.data[1][5]='{"note":"Tampered"}';
  assert.throws(()=>receiver.dispatch({action:'apply',runId,sequence:3,operation:{kind:'publish'}},env.props,1000),/INCOMPLETE/);
});

test('lost prepare state retries within capacity without allocating twice', () => {
  const env=fakeGoogle(),runId='6'.repeat(32);
  env.ss.getSheetById(1).rows=4_999_995;
  receiver.dispatch({action:'begin',runId},env.props,1000);
  const before=env.props.getProperty('RUN');
  const op=operations()[0];
  op.tabs=[{name:'Example',rows:5,cols:2}];
  const apply=()=>receiver.dispatch({action:'apply',runId,sequence:0,operation:op},env.props,1000);
  assert.equal(apply().next,1);
  env.props.setProperty('RUN',before); // Google committed, script state write was lost.
  assert.equal(apply().next,1);
  assert.equal(env.ss.getSheets().length,2);
});

test('publishing works when only the old backup has a visible tab', () => {
  const env=fakeGoogle();
  for (const runId of ['7'.repeat(32),'8'.repeat(32)]) {
    receiver.dispatch({action:'begin',runId},env.props,1000);
    operations().forEach((operation,sequence)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000));
    env.remove(1);
  }
  assert.ok(env.ss.getSheetByName('Backup - Example'));
});

test('a public user tab with a reserved title is never overwritten', () => {
  const env=fakeGoogle();
  env.ss.getSheetById(1).title='Backup - Example';
  const runId='3'.repeat(32);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  assert.throws(()=>receiver.dispatch({action:'apply',runId,sequence:0,operation:{kind:'prepare',tabs:[{name:'Example',rows:1,cols:1}],manifest:{}}},env.props,1000),/COLLISION/);
  assert.equal(env.ss.getSheets().length,1);
});

module.exports={fakeGoogle};
