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

test('presentation titles and column limits are allowlisted', () => {
  assert.equal(receiver.finalTitle({name:'Results',finalTitle:'Results',presentation:'results-v1',version:1}), 'Results');
  assert.equal(receiver.finalTitle({name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1}), 'Recruit Profiles');
  assert.equal(receiver.finalTitle({name:'Photos'}), 'Backup - Photos');
  assert.throws(() => receiver.finalTitle({name:'Anything',finalTitle:'Results',presentation:'results-v1',version:1}), /TABS/);
  assert.equal(receiver.validateTabs([{name:'Results',finalTitle:'Results',presentation:'results-v1',version:1,rows:2,cols:128}],0),256);
  assert.throws(() => receiver.validateTabs([{name:'Results',finalTitle:'Results',presentation:'results-v1',version:1,rows:2,cols:129}],0),/TABS/);
  assert.throws(() => receiver.validateTabs([{name:'Ordinary',rows:2,cols:61}],0),/TABS/);
});

test('unowned unprefixed presentation titles block prepare without changes', () => {
  for (const title of ['Results','Recruit Profiles']) {
    const env=fakeGoogle(),runId=(title==='Results'?'a':'b').repeat(32);
    env.ss.getSheetById(1).title=title;
    receiver.dispatch({action:'begin',runId},env.props,1000);
    const descriptor=title==='Results'
      ? {name:'Results',finalTitle:'Results',presentation:'results-v1',version:1,rows:6,cols:27}
      : {name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1,rows:210,cols:100};
    assert.throws(()=>receiver.dispatch({action:'apply',runId,sequence:0,operation:{kind:'prepare',tabs:[descriptor],manifest:{}}},env.props,1000),/COLLISION/);
    assert.equal(env.ss.getSheets().length,1);
    assert.equal(env.ss.getSheetById(1).title,title);
  }
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
  const colNumber=value=>[...value.toUpperCase()].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);
  const parseA1=value=>{
    const match=String(value).match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/i);
    if(!match) throw new Error(`bad A1 ${value}`);
    const row=Number(match[2]),col=colNumber(match[1]);
    return {row,col,rows:(match[4]?Number(match[4]):row)-row+1,cols:(match[3]?colNumber(match[3]):col)-col+1,a1:String(value).toUpperCase()};
  };
  const add = (id,title,rows=1,cols=2,owner=null) => {
    const s = {id,title,rows,cols,data:[],formulas:new Map(),validations:new Map(),metadata:owner?[tag('evalday_backup_run',owner)]:[],hidden:false,images:[],
      charts:[],conditionalRules:[],protections:[],merges:[],hiddenColumns:new Set(),frozenRows:0,hiddenGridlines:false,tabColor:null,
      getSheetId(){return this.id}, getName(){return this.title}, getMaxRows(){return this.rows}, getMaxColumns(){return this.cols},
      getDeveloperMetadata(){return this.metadata},
      getRange(start,col,n,width){
        const bounds=typeof start==='string'?parseA1(start):{row:start,col,rows:n||1,cols:width||1,a1:null};
        const key=(r,c)=>`${r}:${c}`;
        const range={
          sheet:this,bounds,
          getRow:()=>bounds.row,getColumn:()=>bounds.col,getNumRows:()=>bounds.rows,getNumColumns:()=>bounds.cols,
          getA1Notation:()=>bounds.a1||`${bounds.row}:${bounds.col}:${bounds.rows}:${bounds.cols}`,
          getValues:()=>Array.from({length:bounds.rows},(_,i)=>Array.from({length:bounds.cols},(_,j)=>this.data[bounds.row+i-1]?.[bounds.col+j-1]??'')),
          getValue:()=>this.data[bounds.row-1]?.[bounds.col-1]??'',
          setValues:values=>{values.forEach((row,i)=>row.forEach((value,j)=>{this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]=value;this.formulas.delete(key(bounds.row+i,bounds.col+j));}));return range},
          setValue:value=>range.setValues([[value]]),
          getFormula:()=>this.formulas.get(key(bounds.row,bounds.col))||'',
          getFormulas:()=>Array.from({length:bounds.rows},(_,i)=>Array.from({length:bounds.cols},(_,j)=>this.formulas.get(key(bounds.row+i,bounds.col+j))||'')),
          setFormula:formula=>{this.formulas.set(key(bounds.row,bounds.col),formula);this.data[bounds.row-1]??=[];this.data[bounds.row-1][bounds.col-1]='';return range},
          setFormulas:values=>{values.forEach((row,i)=>row.forEach((formula,j)=>{this.formulas.set(key(bounds.row+i,bounds.col+j),formula);this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]='';}));return range},
          clearContent:()=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++){this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]='';this.formulas.delete(key(bounds.row+i,bounds.col+j));}return range},
          getDataValidation:()=>this.validations.get(key(bounds.row,bounds.col))||null,
          setDataValidation:rule=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++)this.validations.set(key(bounds.row+i,bounds.col+j),rule);return range},
          clearDataValidations:()=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++)this.validations.delete(key(bounds.row+i,bounds.col+j));return range},
          merge:()=>{this.merges.push(range.getA1Notation());return range},
          breakApart:()=>{this.merges=this.merges.filter(value=>value!==range.getA1Notation());return range},
        };
        ['setBackground','setFontColor','setFontWeight','setFontFamily','setFontSize','setWrap','setWrapStrategy',
          'setVerticalAlignment','setHorizontalAlignment','setNumberFormat','setBorder'].forEach(name=>range[name]=()=>range);
        return range;
      },
      getDataRange(){return {getValues:()=>this.data}}, getImages(){return this.images},
      insertImage(blob,col,row){const image={getAnchorCell:()=>({getRow:()=>row,getColumn:()=>col}),remove:()=>{this.images=this.images.filter(i=>i!==image)},setWidth(){return image},setHeight(){return image}};this.images.push(image);return image},
      setRowHeight(){},setColumnWidth(){},setFrozenRows(value){this.frozenRows=value},getFrozenRows(){return this.frozenRows},
      setHiddenGridlines(value){this.hiddenGridlines=value},hideColumns(start,count){for(let i=0;i<count;i++)this.hiddenColumns.add(start+i)},
      isColumnHiddenByUser(col){return this.hiddenColumns.has(col)},setTabColor(value){this.tabColor=value},
      getCharts(){return this.charts},removeChart(chart){this.charts=this.charts.filter(item=>item!==chart)},insertChart(chart){this.charts.push(chart)},
      newChart(){const state={ranges:[],position:null,type:null,options:{}};const builder={setChartType:value=>{state.type=value;return builder},addRange:value=>{state.ranges.push(value);return builder},setPosition:(row,col)=>{state.position={row,col};return builder},setOption:(key,value)=>{state.options[key]=value;return builder},build:()=>({...state})};return builder},
      getConditionalFormatRules(){return this.conditionalRules},setConditionalFormatRules(value){this.conditionalRules=[...value]},
      getProtections(){return this.protections},
      effectiveEditable(a1){if(!this.protections.length)return true;return this.protections.some(p=>p.unprotected.includes(a1))},
    };
    sheets.set(id,s); return s;
  };
  add(1,'My own notes');
  const ss = {getSheets:()=>[...sheets.values()], getSheetById:id=>sheets.get(id),
    getSheetByName:name=>[...sheets.values()].find(s=>s.title===name), getDeveloperMetadata:()=>documentMetadata};
  const validationBuilder=()=>{const rule={range:null,allowInvalid:true};const builder={requireValueInRange:range=>{rule.range=range.getA1Notation();return builder},setAllowInvalid:value=>{rule.allowInvalid=value;return builder},setHelpText:()=>builder,build:()=>({...rule})};return builder};
  const conditionalBuilder=()=>{const rule={text:null,ranges:[]};const builder={whenTextEqualTo:value=>{rule.text=value;return builder},setBackground:()=>builder,setFontColor:()=>builder,setBold:()=>builder,setRanges:ranges=>{rule.ranges=ranges.map(item=>item.getA1Notation());return builder},build:()=>({...rule})};return builder};
  global.SpreadsheetApp = {openById:()=>ss,ProtectionType:{SHEET:'SHEET'},newDataValidation:validationBuilder,newConditionalFormatRule:conditionalBuilder};
  global.Charts={ChartType:{RADAR:'RADAR'}};
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
        if(r.addProtectedRange) {const p=r.addProtectedRange.protectedRange;const sheet=sheets.get(p.range.sheetId);const protection={description:p.description||'',unprotected:[],getDescription(){return this.description},setDescription(value){this.description=value;return this},setUnprotectedRanges(ranges){this.unprotected=ranges.map(range=>range.getA1Notation());return this},getUnprotectedRanges(){return this.unprotected.map(value=>sheet.getRange(value))}};sheet.protections.push(protection);}
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
  return {ss,props,add,failPublish:value=>{failPublish=value},remove:id=>sheets.delete(id)};
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

function presentationState(env, descriptor, rows, runId='c'.repeat(32)) {
  const apply=(sequence,operation)=>receiver.dispatch({action:'apply',runId,sequence,operation},env.props,1000);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  apply(0,{kind:'prepare',tabs:[descriptor],manifest:{photoCount:0,snapshotAt:'2026-10-06T00:00:00Z',workspaceName:'Test',recordsSha256:'x',recordChain:'',recordCount:0}});
  apply(1,{kind:'rows',tab:descriptor.name,start:1,rows});
  apply(2,{kind:'verifyCells',tab:descriptor.name,start:1,count:rows.length,sha256:digest(JSON.stringify(rows))});
  return JSON.parse(env.props.getProperty('RUN'));
}

function resultsPresentation() {
  const rows=Array.from({length:6},()=>Array(27).fill(''));
  rows[2][1]='All completed Journees'; rows[2][4]='Overall ranking';
  rows[0].splice(12,13,'Scope','View','Rank','Recruit','Journee','Score','Scale','Details','Status','Color','General comment','Notes','Lookup key');
  rows[1].splice(12,13,'All completed Journees','Overall ranking','1','Alex','Day','10','/20','Complete','Complete','Green','','','All completed Journees|Overall ranking|1');
  rows[0][25]='Scope options'; rows[1][25]='All completed Journees'; rows[0][26]='View options'; rows[1][26]='Overall ranking';
  const layout={selectorCells:['B3','E3'],headerRow:5,visibleStartRow:6,visibleCapacity:1,frozenRows:5,helperStartCol:13,
    tabColor:'GREEN',columnWidths:[70,210,185,95,65,155,100,90,265,265],blocks:{
      results:{startRow:1,endRow:2,startCol:13,endCol:25},scopeOptions:{startRow:1,endRow:2,startCol:26,endCol:26},viewOptions:{startRow:1,endRow:2,startCol:27,endCol:27}}};
  return {rows,layout};
}

function profilePresentation() {
  const rows=Array.from({length:203},()=>Array(96).fill(''));
  rows[2][1]='All completed Journees'; rows[2][4]='Alex';
  const set=(r,c,values)=>values.forEach((value,index)=>rows[r-1][c-1+index]=String(value));
  set(1,27,['Selection','Profile key','Journee','Date','Recruit','Phone','DOB','Attendance','Arrival','Attendance comment','Overall','Display rank','Overall rank','Overall population','Journee rank','Journee population','Color','Missing','Punctuality','Respect','Seriousness','General average','General comment','Notes']);
  set(2,27,['All completed Journees|Alex','j:r','Day','2026-10-06','Alex','123','2000-01-01','Present','08:00','','10','1','1','1','1','1','Green','Complete','1','1','1','1','','']);
  set(1,51,['Selection','Dimension','Score','Rank','Status','Coverage']);set(2,51,['All completed Journees|Alex','Dimension','4','1','Complete','100%']);
  set(1,57,['Selection','Activity','Score','Rank','Submissions','Status']);set(2,57,['All completed Journees|Alex','Activity','4','1','1/1','Complete']);
  set(1,63,['Profile key','Activity','Evaluator','Category','Score','Status','Comment']);set(2,63,['j:r','Activity','Eva','Overall','4','Complete','']);
  set(1,70,['Profile key','Activity','Dimension','Criterion','Explanation','Evaluator','Grade','Raw result','Status']);set(2,70,['j:r','Activity','Dimension','Criterion','Why','Eva','4','','Complete']);
  set(1,79,['Profile key','Date','Username','Action','Reason','Before','After']);set(2,79,['j:r','2026-10-06','Admin','Updated','','','']);
  set(1,86,['Scope options']);set(2,86,['All completed Journees']);
  set(1,87,['Scope','Label','Profile key']);set(2,87,['All completed Journees','Alex','j:r']);
  set(1,90,['Recruit options','Profile key']);set(2,90,['Alex','j:r']);
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
  set(1,92,['Profile key','Part','Parts','SHA-256','PNG chunk']);set(2,92,['j:r','0','1',digest(png),png.toString('base64')]);
  const block=(start,end,width)=>({startRow:1,endRow:2,startCol:start,endCol:start+width-1});
  const layout={selectorCells:['B3','E3'],profileKeyCell:'H3',imageAnchor:'J3',frozenRows:7,helperStartCol:27,tabColor:'YELLOW',
    columnWidths:[165,125,24,145,125,24,120,125,90,75,75,75],expectedPreviewCount:1,dimensionCount:1,activityCount:1,
    factorCount:3,evaluatorCapacity:1,criterionCapacity:1,auditCapacity:1,sectionRows:{dimension:8,dimensionHeader:9,dimensionStart:10,
      activity:26,activityHeader:27,activityStart:28,general:44,generalStart:45,evaluator:55,evaluatorHeader:56,evaluatorStart:57,
      criterion:79,criterionHeader:80,criterionStart:81,audit:200,auditHeader:201,auditStart:202},
    charts:[{startRow:10,endRow:10,labelCol:1,valueCol:2,anchor:'G8',maximum:5},{startRow:28,endRow:28,labelCol:1,valueCol:2,anchor:'G26',maximum:5}],
    blocks:{summaries:block(27,50,24),dimensions:block(51,56,6),activities:block(57,62,6),evaluators:block(63,69,7),criteria:block(70,78,9),
      audit:block(79,85,7),scopeOptions:block(86,86,1),profileOptions:block(87,89,3),dependentOptions:block(90,91,2),previews:block(92,96,5)}};
  return {rows,layout};
}

test('trusted layouts replay without duplicate owned objects and keep only selectors editable', () => {
  for (const [name,presentation,fixture,expectedCharts,expectedImages] of [
    ['Results','results-v1',resultsPresentation(),0,0],['Recruit Profiles','recruit-profiles-v1',profilePresentation(),2,1],
  ]) {
    const env=fakeGoogle();
    const descriptor={name,finalTitle:name,presentation,version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
    const state=presentationState(env,descriptor,fixture.rows,name==='Results'?'d'.repeat(32):'e'.repeat(32));
    const op={kind:'layout',version:1,tab:name,presentation,layout:fixture.layout};
    assert.doesNotThrow(()=>receiver.applyPresentationLayout(op,state,env.ss));
    assert.doesNotThrow(()=>receiver.applyPresentationLayout(op,state,env.ss));
    const sheet=env.ss.getSheetById(state.tabs[0].id);
    assert.equal(sheet.getCharts().length,expectedCharts);
    assert.equal(sheet.getImages().length,expectedImages);
    assert.equal(sheet.getProtections().filter(item=>item.getDescription()==='Evalday interactive presentation selectors').length,1);
    assert.deepEqual(sheet.getProtections()[0].unprotected.sort(),['B3','E3']);
    assert.equal(sheet.effectiveEditable('B3'),true);
    assert.equal(sheet.effectiveEditable('E3'),true);
    assert.equal(sheet.effectiveEditable('A1'),false);
    assert.doesNotThrow(()=>receiver.verifyPresentationLayout({kind:'verifyLayout',version:1,tab:name,presentation,layout:fixture.layout},state,env.ss));
  }
});

module.exports={fakeGoogle};
