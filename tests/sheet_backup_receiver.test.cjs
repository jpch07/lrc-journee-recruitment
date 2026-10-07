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

test('row writes verify through Sheets when SpreadsheetApp still serves a stale cache', () => {
  const env=fakeGoogle(),runId='d'.repeat(32),rows=[['Selection','',''],['All completed Journees|Alex','=IMPORTXML("bad")','']];
  receiver.dispatch({action:'begin',runId},env.props,1000);
  receiver.dispatch({action:'apply',runId,sequence:0,operation:{kind:'prepare',tabs:[{name:'Example',rows:2,cols:3}],manifest:{}}},env.props,1000);
  env.staleSpreadsheetReads(true);
  const result=receiver.dispatch({action:'apply',runId,sequence:1,operation:{kind:'rows',tab:'Example',start:1,rows}},env.props,1000);
  assert.equal(result.next,2);
  const verified=receiver.dispatch({action:'apply',runId,sequence:2,operation:{kind:'verifyCells',tab:'Example',start:1,count:2,sha256:digest(JSON.stringify(rows))}},env.props,1000);
  assert.equal(verified.next,3);
  assert.deepEqual(env.ss.getSheetByName('_stage_dddddddddddd_Example').data,rows);
});

test('preview decoder accepts canonical padded legacy and URL-safe base64 alphabets', () => {
  fakeGoogle();
  const png=Buffer.from('89504e470d0a1a0a00000fbf','hex');
  const legacy=png.toString('base64');
  const webSafe=legacy.replace(/\+/g,'-').replace(/\//g,'_');
  assert.ok(legacy.includes('+') && legacy.includes('/'));
  assert.deepEqual(Buffer.from(receiver.decodePreviewBase64_(legacy)),png);
  assert.deepEqual(Buffer.from(receiver.decodePreviewBase64_(webSafe)),png);
});

test('preview decoder rejects malformed, mixed-alphabet, and provider-rejected base64 as VERIFY', () => {
  fakeGoogle();
  for (const value of [
    'iVBORw0KGgoAAA-',
    'iVBORw0KGgoAAA+_',
    'iVBORw0KGgoAA=+/',
    'AB==',
    'iVBORw0KGgoA[credential-bearing URL excluded]',
  ]) assert.throws(()=>receiver.decodePreviewBase64_(value),/VERIFY/);
  const original=Utilities.base64Decode;
  Utilities.base64Decode=()=>{throw new Error('Could not decode string')};
  try {
    assert.throws(()=>receiver.decodePreviewBase64_('iVBORw0KGgoAAA+/'),/VERIFY/);
  } finally {
    Utilities.base64Decode=original;
  }
});

function fakeGoogle() {
  const sheets = new Map();
  let documentMetadata = [];
  let failPublish = false;
  let rejectPerSheetMetadata = false;
  let rejectSheetList = false;
  let omitDefaultSheetId = false;
  let staleSpreadsheetReads = false;
  let rejectTransientInvalid = false;
  const tag = (key,value,id=1,onRemove=()=>{}) => ({getKey:()=>key,getValue:()=>value,getId:()=>id,remove:onRemove});
  const colNumber=value=>[...value.toUpperCase()].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);
  const parseA1=value=>{
    const match=String(value).match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/i);
    if(!match) throw new Error(`bad A1 ${value}`);
    const row=Number(match[2]),col=colNumber(match[1]);
    return {row,col,rows:(match[4]?Number(match[4]):row)-row+1,cols:(match[3]?colNumber(match[3]):col)-col+1,a1:String(value).toUpperCase()};
  };
  const add = (id,title,rows=1,cols=2,owner=null) => {
    const s = {id,title,rows,cols,data:[],formulas:new Map(),numberFormats:new Map(),validations:new Map(),metadata:owner?[tag('evalday_backup_run',owner)]:[],hidden:false,images:[],
      charts:[],conditionalRules:[],protections:[],merges:[],hiddenColumns:new Set(),frozenRows:0,hiddenGridlines:false,tabColor:null,
      getSheetId(){return this.id}, getName(){return this.title}, getMaxRows(){return this.rows}, getMaxColumns(){return this.cols},
      getDeveloperMetadata(){if(rejectPerSheetMetadata)throw new Error('per-sheet metadata lookup rejected');return this.metadata},
      addDeveloperMetadata(key,value){const item=tag(key,value,this.metadata.length+10,()=>{this.metadata=this.metadata.filter(entry=>entry!==item)});this.metadata.push(item);return item},
      getRange(start,col,n,width){
        const bounds=typeof start==='string'?parseA1(start):{row:start,col,rows:n||1,cols:width||1,a1:null};
        const key=(r,c)=>`${r}:${c}`;
        const range={
          sheet:this,bounds,getSheet:()=>this,
          getRow:()=>bounds.row,getColumn:()=>bounds.col,getNumRows:()=>bounds.rows,getNumColumns:()=>bounds.cols,
          getA1Notation:()=>bounds.a1||`${bounds.row}:${bounds.col}:${bounds.rows}:${bounds.cols}`,
          getValues:()=>Array.from({length:bounds.rows},(_,i)=>Array.from({length:bounds.cols},(_,j)=>
            staleSpreadsheetReads ? '' : this.data[bounds.row+i-1]?.[bounds.col+j-1]??'')),
          getValue:()=>staleSpreadsheetReads ? '' : this.data[bounds.row-1]?.[bounds.col-1]??'',
          setValues:values=>{values.forEach((row,i)=>row.forEach((value,j)=>{this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]=value;this.formulas.delete(key(bounds.row+i,bounds.col+j));}));this.assertValidations();return range},
          setValue:value=>range.setValues([[value]]),
          getFormula:()=>this.formulas.get(key(bounds.row,bounds.col))||'',
          getFormulas:()=>Array.from({length:bounds.rows},(_,i)=>Array.from({length:bounds.cols},(_,j)=>this.formulas.get(key(bounds.row+i,bounds.col+j))||'')),
          setFormula:formula=>{this.formulas.set(key(bounds.row,bounds.col),formula);this.data[bounds.row-1]??=[];this.data[bounds.row-1][bounds.col-1]='';return range},
          setFormulas:values=>{values.forEach((row,i)=>row.forEach((formula,j)=>{this.formulas.set(key(bounds.row+i,bounds.col+j),formula);this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]='';}));return range},
          clearContent:()=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++){this.data[bounds.row+i-1]??=[];this.data[bounds.row+i-1][bounds.col+j-1]='';this.formulas.delete(key(bounds.row+i,bounds.col+j));}this.assertValidations();return range},
          getNumberFormat:()=>this.numberFormats.get(key(bounds.row,bounds.col))||'',
          setNumberFormat:value=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++)this.numberFormats.set(key(bounds.row+i,bounds.col+j),value);return range},
          getDataValidation:()=>this.validations.get(key(bounds.row,bounds.col))||null,
          setDataValidation:rule=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++)this.validations.set(key(bounds.row+i,bounds.col+j),rule);this.assertValidations();return range},
          clearDataValidations:()=>{for(let i=0;i<bounds.rows;i++)for(let j=0;j<bounds.cols;j++)this.validations.delete(key(bounds.row+i,bounds.col+j));this.assertValidations();return range},
          merge:()=>{this.merges.push(range.getA1Notation());return range},
          breakApart:()=>{this.merges=this.merges.filter(value=>value!==range.getA1Notation());return range},
        };
        ['setBackground','setFontColor','setFontWeight','setFontFamily','setFontSize','setWrap','setWrapStrategy',
          'setVerticalAlignment','setHorizontalAlignment','setBorder'].forEach(name=>range[name]=()=>range);
        return range;
      },
      assertValidations(){
        if(!rejectTransientInvalid)return;
        this.validations.forEach((rule,target)=>{
          if(rule.allowInvalid || !rule.rangeObject)return;
          const [row,col]=target.split(':').map(Number),value=String(this.data[row-1]?.[col-1]??'');
          if(!value)return;
          assert.ok(rule.rangeObject.getValues().flat().map(String).includes(value),`transient invalid validation at ${target}`);
        });
      },
      getDataRange(){return {getValues:()=>this.data}}, getImages(){return this.images},
      insertImage(blob,col,row){let anchorCol=col,anchorRow=row;const image={blob,offsetHistory:[],replace(nextBlob){image.blob=nextBlob;image.replaceCalls+=1;return image},replaceCalls:0,getAnchorCell:()=>({getRow:()=>anchorRow,getColumn:()=>anchorCol}),remove:()=>{this.images=this.images.filter(i=>i!==image)},setAnchorCell(range){anchorRow=range.getRow();anchorCol=range.getColumn();return image},setAnchorCellXOffset(value){image.offsetHistory.push(value);return image},setWidth(){return image},setHeight(){return image}};this.images.push(image);return image},
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
  const ss = {getId:()=> '11YSIJSpXWZZg00HlldQg3NLwKWQ-tGfrmQPPQF8Gbk0',getSheets:()=>{if(rejectSheetList)throw new Error('sheet list lookup rejected');return [...sheets.values()]}, getSheetById:id=>sheets.get(id),
    getSheetByName:name=>[...sheets.values()].find(s=>s.title===name), getDeveloperMetadata:()=>documentMetadata};
  const validationBuilder=()=>{const rule={range:null,rangeObject:null,allowInvalid:true};const builder={requireValueInRange:range=>{rule.range=range.getA1Notation();rule.rangeObject=range;return builder},setAllowInvalid:value=>{rule.allowInvalid=value;return builder},setHelpText:()=>builder,build:()=>({...rule,getCriteriaType:()=> 'VALUE_IN_RANGE',getCriteriaValues:()=>[rule.rangeObject,true],getAllowInvalid:()=>rule.allowInvalid})};return builder};
  const conditionalBuilder=()=>{const rule={text:null,ranges:[]};const builder={whenTextEqualTo:value=>{rule.text=value;return builder},setBackground:()=>builder,setFontColor:()=>builder,setBold:()=>builder,setRanges:ranges=>{rule.ranges=ranges.map(item=>item.getA1Notation());return builder},build:()=>({...rule})};return builder};
  global.SpreadsheetApp = {openById:()=>ss,flush:()=>{},ProtectionType:{SHEET:'SHEET'},DataValidationCriteria:{VALUE_IN_RANGE:'VALUE_IN_RANGE'},newDataValidation:validationBuilder,newConditionalFormatRule:conditionalBuilder};
  global.Charts={ChartType:{RADAR:'RADAR'}};
  let triggers=[];
  global.ScriptApp={getProjectTriggers:()=>[...triggers],deleteTrigger:trigger=>{triggers=triggers.filter(item=>item!==trigger)},newTrigger:handler=>{
    const state={handler,sourceId:null};const builder={forSpreadsheet:id=>{state.sourceId=id;return builder},onEdit:()=>builder,
      create:()=>{const trigger={getHandlerFunction:()=>state.handler,getTriggerSourceId:()=>state.sourceId};triggers.push(trigger);return trigger}};return builder;
  }};
  const strictBase64Decode=value=>{
    if(typeof value!=='string'||value.length%4!==0||!/^[A-Za-z0-9+/]*={0,2}$/.test(value)) throw new Error('Could not decode string');
    const bytes=Buffer.from(value,'base64');
    if(bytes.toString('base64')!==value) throw new Error('Could not decode string');
    return [...bytes];
  };
  global.Utilities = {Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},
    computeDigest:(_,value)=>[...crypto.createHash('sha256').update(Array.isArray(value)?Buffer.from(value):value).digest()],
    base64Decode:strictBase64Decode,
    base64Encode:value=>Buffer.from(value).toString('base64'),
    newBlob:value=>({getBytes:()=>[...Buffer.from(value)]})};
  global.Sheets = {Spreadsheets:{
    get:()=>({sheets:[...sheets.values()].map(sheet=>({
      properties:{...(omitDefaultSheetId && sheet.id===0 ? {} : {sheetId:sheet.id}),title:sheet.title,gridProperties:{rowCount:sheet.rows,columnCount:sheet.cols}},
      developerMetadata:sheet.metadata.map(item=>({metadataKey:item.getKey(),metadataValue:item.getValue()})),
    }))}),
    Values:{
      update:(body,id,range,options)=>{
        assert.equal(options.valueInputOption,'RAW');
        const [,name,row] = range.match(/^'(.+)'!A(\d+)(?::[A-Z]+\d+)?$/);
        const s=ss.getSheetByName(name.replace(/''/g,"'"));
        body.values.forEach((r,i)=>s.data[Number(row)-1+i]=r);
      },
      get:(id,range,options)=>{
        assert.equal(options.valueRenderOption,'UNFORMATTED_VALUE');
        assert.equal(options.dateTimeRenderOption,'SERIAL_NUMBER');
        const [,name,startCol,startRow,endCol,endRow] = range.match(/^'(.+)'!([A-Z]+)(\d+):([A-Z]+)(\d+)$/);
        const s=ss.getSheetByName(name.replace(/''/g,"'")), first=colNumber(startCol)-1,width=colNumber(endCol)-first;
        const values=Array.from({length:Number(endRow)-Number(startRow)+1},(_,i)=>(s.data[Number(startRow)-1+i]||[]).slice(first,first+width));
        values.forEach(row=>{while(row.length && (row.at(-1)===null || row.at(-1)===''))row.pop()});
        while(values.length && !values.at(-1).length)values.pop();
        return {values};
      },
    },
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
  return {ss,props,add,publish:runId=>{documentMetadata=[tag('evalday_backup_published',JSON.stringify({runId}),99)]},triggers:()=>[...triggers],
    failPublish:value=>{failPublish=value},rejectPerSheetMetadata:value=>{rejectPerSheetMetadata=value},rejectSheetList:value=>{rejectSheetList=value},
    omitDefaultSheetId:value=>{omitDefaultSheetId=value},staleSpreadsheetReads:value=>{staleSpreadsheetReads=value},
    rejectTransientInvalid:value=>{rejectTransientInvalid=value},remove:id=>sheets.delete(id)};
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

test('lost prepare response reconciles staging from one inventory without per-sheet metadata calls', () => {
  const env=fakeGoogle(),runId='a6'.repeat(16);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  const before=env.props.getProperty('RUN');
  const tabs=Array.from({length:37},(_,index)=>({name:`Tab ${index}`,rows:2,cols:2}));
  const operation={kind:'prepare',tabs,manifest:{photoCount:0,snapshotAt:'2026-10-06T00:00:00Z',workspaceName:'Test',recordsSha256:'x',recordChain:'',recordCount:0}};
  const apply=()=>receiver.dispatch({action:'apply',runId,sequence:0,operation},env.props,1000);
  assert.equal(apply().next,1);
  env.props.setProperty('RUN',before); // Google committed, but the receiver response/state acknowledgement was lost.
  env.rejectPerSheetMetadata(true);
  env.rejectSheetList(true);
  assert.equal(apply().next,1);
  env.rejectSheetList(false);
  assert.equal(env.ss.getSheets().length,38);
});

test('inventory accepts an omitted default sheet id as id zero', () => {
  const env=fakeGoogle(),runId='b6'.repeat(16);
  env.add(0,'Sheet1',1000,26);
  env.omitDefaultSheetId(true);
  receiver.dispatch({action:'begin',runId},env.props,1000);
  const operation={kind:'prepare',tabs:[{name:'Example',rows:2,cols:2}],manifest:{photoCount:0,snapshotAt:'2026-10-06T00:00:00Z',workspaceName:'Test',recordsSha256:'x',recordChain:'',recordCount:0}};
  assert.equal(receiver.dispatch({action:'apply',runId,sequence:0,operation},env.props,1000).next,1);
  assert.equal(env.ss.getSheetById(0).title,'Sheet1');
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
    bandStyles:[{label:'Green',background:'#16834B',font:'#FFFFFF'},{label:'Yellow',background:'#E3AD22',font:'#223449'},{label:'Red',background:'#C8102E',font:'#FFFFFF'}],
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
  set(3,27,['All completed Journees|Alex — Other · 2','j2:r2','Other','2026-10-07','Alex','456','2000-01-02','Present','09:00','','9','2','2','2','1','1','Yellow','Complete','1','1','1','1','','']);
  set(4,27,['Day|Day Alex','j:r','Day','2026-10-06','Alex','123','2000-01-01','Present','08:00','','10','1','1','1','1','1','Green','Complete','1','1','1','1','','']);
  set(1,51,['Selection','Dimension','Score','Rank','Status','Coverage']);set(2,51,['All completed Journees|Alex','Dimension','4','1','Complete','100%']);
  set(1,57,['Selection','Activity','Score','Rank','Submissions','Status']);set(2,57,['All completed Journees|Alex','Activity','4','1','1/1','Complete']);
  set(1,63,['Profile key','Activity','Evaluator','Category','Score','Status','Comment']);set(2,63,['j:r','Activity','Eva','Overall','4','Complete','']);
  set(1,70,['Profile key','Activity','Dimension','Criterion','Explanation','Evaluator','Grade','Raw result','Status']);set(2,70,['j:r','Activity','Dimension','Criterion','Why','Eva','4','','Complete']);
  set(1,79,['Profile key','Date','Username','Action','Reason','Before','After']);set(2,79,['j:r','2026-10-06','Admin','Updated','','','']);
  set(1,86,['Scope options']);set(2,86,['All completed Journees']);set(3,86,['Day']);set(4,86,['Empty']);
  set(1,87,['Scope','Label','Profile key']);set(2,87,['All completed Journees','Alex','j:r']);
  set(3,87,['All completed Journees','Alex — Other · 2','j2:r2']);set(4,87,['Day','Day Alex','j:r']);
  set(1,90,['Recruit options','Profile key']);set(2,90,['Alex','j:r']);set(3,90,['Alex — Other · 2','j2:r2']);
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
  const secondPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlB8AAAAASUVORK5CYII=','base64');
  const webSafe=value=>value.toString('base64').replace(/\+/g,'-').replace(/\//g,'_');
  set(1,92,['Profile key','Part','Parts','SHA-256','PNG chunk']);set(2,92,['j:r','0','1',digest(png),webSafe(png)]);
  set(3,92,['j2:r2','0','1',digest(secondPng),webSafe(secondPng)]);
  const block=(start,end,width)=>({startRow:1,endRow:2,startCol:start,endCol:start+width-1});
  const layout={selectorCells:['B3','E3'],profileKeyCell:'H3',imageAnchor:'J3',frozenRows:7,helperStartCol:27,tabColor:'YELLOW',
    columnWidths:[165,125,24,145,125,24,120,125,90,75,75,75],expectedPreviewCount:2,dimensionCount:1,activityCount:1,
    factorCount:3,evaluatorCapacity:1,criterionCapacity:1,auditCapacity:1,
    bandStyles:[{label:'Green',background:'#16834B',font:'#FFFFFF'},{label:'Yellow',background:'#E3AD22',font:'#223449'},{label:'Red',background:'#C8102E',font:'#FFFFFF'}],sectionRows:{dimension:8,dimensionHeader:9,dimensionStart:10,
      activity:26,activityHeader:27,activityStart:28,general:44,generalStart:45,evaluator:55,evaluatorHeader:56,evaluatorStart:57,
      criterion:79,criterionHeader:80,criterionStart:81,audit:200,auditHeader:201,auditStart:202},
    charts:[{startRow:10,endRow:10,labelCol:1,valueCol:2,anchor:'G8',maximum:5},{startRow:28,endRow:28,labelCol:1,valueCol:2,anchor:'G26',maximum:5}],
    blocks:{summaries:{startRow:1,endRow:4,startCol:27,endCol:50},dimensions:block(51,56,6),activities:block(57,62,6),evaluators:block(63,69,7),criteria:block(70,78,9),
      audit:block(79,85,7),scopeOptions:{startRow:1,endRow:4,startCol:86,endCol:86},profileOptions:{startRow:1,endRow:4,startCol:87,endCol:89},
      dependentOptions:{startRow:1,endRow:3,startCol:90,endCol:91},previews:{startRow:1,endRow:3,startCol:92,endCol:96}}};
  return {rows,layout,secondDigest:digest(secondPng)};
}

function resultsPresentationV2() {
  const rows=Array.from({length:6},()=>Array(28).fill(''));
  rows[2][1]='All completed Journees'; rows[2][4]='Overall ranking';
  rows[0].splice(12,12,'Scope key','View key','Rank','Recruit','Journee','Score','Scale','Details','Status','Color','General comment','Notes');
  rows[1].splice(12,12,'completed','overall','1','Alex','Day','10','/20','Complete','Complete','Green','','');
  rows[0].splice(24,2,'Scope option','Scope key'); rows[1].splice(24,2,'All completed Journees','completed');
  rows[0].splice(26,2,'View option','View key'); rows[1].splice(26,2,'Overall ranking','overall');
  const layout={selectorCells:['B3','E3'],headerRow:5,visibleStartRow:6,visibleCapacity:1,frozenRows:5,helperStartCol:13,
    bandStyles:[{label:'Green',background:'#16834B',font:'#FFFFFF'},{label:'Yellow',background:'#E3AD22',font:'#223449'},{label:'Red',background:'#C8102E',font:'#FFFFFF'}],
    tabColor:'GREEN',columnWidths:[70,210,185,95,65,155,100,90,265,265],blocks:{
      results:{startRow:1,endRow:2,startCol:13,endCol:24},scopeOptions:{startRow:1,endRow:2,startCol:25,endCol:26},viewOptions:{startRow:1,endRow:2,startCol:27,endCol:28}}};
  return {rows,layout};
}

function profilePresentationV2() {
  const rows=Array.from({length:203},()=>Array(97).fill(''));
  rows[2][1]='All completed Journees'; rows[2][4]='Alex';
  const set=(r,c,values)=>values.forEach((value,index)=>rows[r-1][c-1+index]=String(value));
  set(1,27,['Scope key','Profile key','Journee','Date','Recruit','Phone','DOB','Attendance','Arrival','Attendance comment','Overall','Display rank','Overall rank','Overall population','Journee rank','Journee population','Color','Missing','Punctuality','Respect','Seriousness','General average','General comment','Notes']);
  set(2,27,['completed','j:r','Day','2026-10-06','Alex','123','2000-01-01','Present','08:00','','10','1','1','1','1','1','Green','Complete','1','1','1','1','','']);
  set(3,27,['completed','j2:r2','Other','2026-10-07','Alex','456','2000-01-02','Present','09:00','','9','2','2','2','1','1','Yellow','Complete','1','1','1','1','','']);
  set(4,27,['journey:j','j:r','Day','2026-10-06','Alex','123','2000-01-01','Present','08:00','','10','1','1','1','1','1','Green','Complete','1','1','1','1','','']);
  set(1,51,['Scope key','Profile key','Score','Rank','Status','Coverage']);set(2,51,['completed','j:r','4','1','Complete','100%']);
  set(1,57,['Scope key','Profile key','Score','Rank','Submissions','Status']);set(2,57,['completed','j:r','4','1','1/1','Complete']);
  set(1,63,['Profile key','Activity','Evaluator','Category','Score','Status','Comment']);set(2,63,['j:r','Activity','Eva','Overall','4','Complete','']);
  set(1,70,['Profile key','Activity','Dimension','Criterion','Explanation','Evaluator','Grade','Raw result','Status']);set(2,70,['j:r','Activity','Dimension','Criterion','Why','Eva','4','','Complete']);
  set(1,79,['Profile key','Date','Username','Action','Reason','Before','After']);set(2,79,['j:r','2026-10-06','Admin','Updated','','','']);
  set(1,86,['Scope option','Scope key']);set(2,86,['All completed Journees','completed']);set(3,86,['Day','journey:j']);set(4,86,['Empty','journey:empty']);
  set(1,88,['Scope key','Label','Profile key']);set(2,88,['completed','Alex','j:r']);
  set(3,88,['completed','Alex — Other · 2','j2:r2']);set(4,88,['journey:j','Day Alex','j:r']);
  set(1,91,['Recruit options','Profile key']);set(2,91,['Alex','j:r']);set(3,91,['Alex — Other · 2','j2:r2']);
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
  const secondPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlB8AAAAASUVORK5CYII=','base64');
  const webSafe=value=>value.toString('base64').replace(/\+/g,'-').replace(/\//g,'_');
  set(1,93,['Profile key','Part','Parts','SHA-256','PNG chunk']);set(2,93,['j:r','0','1',digest(png),webSafe(png)]);
  set(3,93,['j2:r2','0','1',digest(secondPng),webSafe(secondPng)]);
  const block=(start,width)=>({startRow:1,endRow:2,startCol:start,endCol:start+width-1});
  const layout={selectorCells:['B3','E3'],profileKeyCell:'H3',imageAnchor:'J3',frozenRows:7,helperStartCol:27,tabColor:'YELLOW',
    columnWidths:[165,125,24,145,125,24,120,125,90,75,75,75],expectedPreviewCount:2,dimensionCount:1,activityCount:1,
    factorCount:3,evaluatorCapacity:1,criterionCapacity:1,auditCapacity:1,
    bandStyles:[{label:'Green',background:'#16834B',font:'#FFFFFF'},{label:'Yellow',background:'#E3AD22',font:'#223449'},{label:'Red',background:'#C8102E',font:'#FFFFFF'}],sectionRows:{dimension:8,dimensionHeader:9,dimensionStart:10,
      activity:26,activityHeader:27,activityStart:28,general:44,generalStart:45,evaluator:55,evaluatorHeader:56,evaluatorStart:57,
      criterion:79,criterionHeader:80,criterionStart:81,audit:200,auditHeader:201,auditStart:202},
    charts:[{startRow:10,endRow:10,labelCol:1,valueCol:2,anchor:'G8',maximum:5},{startRow:28,endRow:28,labelCol:1,valueCol:2,anchor:'G26',maximum:5}],
    blocks:{summaries:{startRow:1,endRow:4,startCol:27,endCol:50},dimensions:block(51,6),activities:block(57,6),evaluators:block(63,7),criteria:block(70,9),
      audit:block(79,7),scopeOptions:{startRow:1,endRow:4,startCol:86,endCol:87},profileOptions:{startRow:1,endRow:4,startCol:88,endCol:90},
      dependentOptions:{startRow:1,endRow:3,startCol:91,endCol:92},previews:{startRow:1,endRow:3,startCol:93,endCol:97}}};
  return {rows,layout};
}

test('profile stable keys stay formula-backed while their rendered value is hidden', () => {
  for (const [presentation,fixture,runId] of [
    ['recruit-profiles-v1',profilePresentation(),'4'.repeat(32)],
    ['recruit-profiles-v2',profilePresentationV2(),'5'.repeat(32)],
  ]) {
    const env=fakeGoogle();
    const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation,version:1,
      rows:fixture.rows.length,cols:fixture.rows[0].length};
    const state=presentationState(env,descriptor,fixture.rows,runId);
    const op={kind:'layout',version:1,tab:'Recruit Profiles',presentation,layout:fixture.layout};
    receiver.applyPresentationLayout(op,state,env.ss);
    const profileKey=env.ss.getSheetById(state.tabs[0].id).getRange(fixture.layout.profileKeyCell);
    assert.match(profileKey.getFormula(),/^=/);
    assert.equal(profileKey.getNumberFormat(),';;;');
    assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss));
  }
});

test('v2 layouts resolve native selectors exclusively through stable helper ids', () => {
  for (const [name,presentation,fixture] of [
    ['Results','results-v2',resultsPresentationV2()],['Recruit Profiles','recruit-profiles-v2',profilePresentationV2()],
  ]) {
    const env=fakeGoogle();
    const descriptor={name,finalTitle:name,presentation,version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
    const state=presentationState(env,descriptor,fixture.rows,name==='Results'?'6'.repeat(32):'7'.repeat(32));
    const op={kind:'layout',version:1,tab:name,presentation,layout:fixture.layout};
    assert.doesNotThrow(()=>receiver.applyPresentationLayout(op,state,env.ss));
    assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss));
    const sheet=env.ss.getSheetById(state.tabs[0].id);
    const formula=sheet.getRange(name==='Results'?'A6':'B10').getFormulas()[0][0];
    assert.match(formula,/FILTER\(/);
    assert.doesNotMatch(formula,/\$B\$3&"\|"&\$E\$3/);
  }
});

test('v2 profile geometry expands and exposes more than 116 criterion rows', () => {
  const fixture=profilePresentationV2(),layout=JSON.parse(JSON.stringify(fixture.layout));
  layout.dimensionCount=17;
  layout.criterionCapacity=117;
  Object.assign(layout.sectionRows,{activity:27,activityHeader:28,activityStart:29,general:45,generalStart:46});
  layout.charts=[
    {startRow:10,endRow:26,labelCol:1,valueCol:2,anchor:'G8',maximum:5},
    {startRow:29,endRow:29,labelCol:1,valueCol:2,anchor:'G27',maximum:5},
  ];
  const env=fakeGoogle(),descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v2',version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,'8'.repeat(32));
  const op={kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v2',layout};
  assert.doesNotThrow(()=>receiver.applyPresentationLayout(op,state,env.ss));
  assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss));
  const invalid=JSON.parse(JSON.stringify(layout));
  invalid.sectionRows.activity=26;
  assert.throws(()=>receiver.applyPresentationLayout({...op,layout:invalid},state,env.ss),/TABS/);
});

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

test('profile layout and verification bypass a stale SpreadsheetApp value cache', () => {
  const env=fakeGoogle(),fixture=profilePresentation();
  const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1,
    rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,'9'.repeat(32));
  const op={kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v1',layout:fixture.layout};
  env.staleSpreadsheetReads(true);
  assert.doesNotThrow(()=>receiver.applyPresentationLayout(op,state,env.ss));
  assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss));
  assert.equal(env.ss.getSheetById(state.tabs[0].id).getImages().length,1);
});

test('legacy standard-base64 profile stores survive layout publication and selector refresh', () => {
  const env=fakeGoogle(),fixture=profilePresentation(),runId='b'.repeat(32);
  const preview=fixture.layout.blocks.previews;
  for(let row=preview.startRow+1;row<=preview.endRow;row++) {
    const index=preview.endCol-1;
    fixture.rows[row-1][index]=fixture.rows[row-1][index].replace(/-/g,'+').replace(/_/g,'/');
  }
  const encoded=fixture.rows.slice(preview.startRow,preview.endRow).map(row=>row[preview.endCol-1]).join('');
  assert.ok(encoded.includes('+') && encoded.includes('/'));
  const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1,
    rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,runId);
  const layout={kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v1',layout:fixture.layout};
  assert.doesNotThrow(()=>receiver.applyPresentationLayout(layout,state,env.ss));
  assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...layout,kind:'verifyLayout'},state,env.ss));
  const sheet=env.ss.getSheetById(state.tabs[0].id);
  sheet.title='Recruit Profiles';
  env.publish(runId);
  sheet.getRange('E3').setValue('Alex — Other · 2');
  assert.doesNotThrow(()=>receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('E3')}));
  assert.equal(digest(Buffer.from(sheet.getImages()[0].blob.getBytes())),fixture.secondDigest);
});

test('layout verification rejects changed or missing prescribed formulas', () => {
  const env=fakeGoogle(),fixture=resultsPresentation();
  const descriptor={name:'Results',finalTitle:'Results',presentation:'results-v1',version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,'1'.repeat(32));
  const op={kind:'layout',version:1,tab:'Results',presentation:'results-v1',layout:fixture.layout};
  receiver.applyPresentationLayout(op,state,env.ss);
  const sheet=env.ss.getSheetById(state.tabs[0].id);
  sheet.getRange('A6').setFormula('=12345');
  assert.throws(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss),/VERIFY/);
  receiver.applyPresentationLayout(op,state,env.ss);
  sheet.getRange('J6').clearContent();
  assert.throws(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss),/VERIFY/);
});

test('profile layout verification checks every preview and uses numeric chart formulas', () => {
  const env=fakeGoogle(),fixture=profilePresentation();
  const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,'2'.repeat(32));
  const op={kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v1',layout:fixture.layout};
  receiver.applyPresentationLayout(op,state,env.ss);
  const sheet=env.ss.getSheetById(state.tabs[0].id);
  assert.match(sheet.getRange('B10').getFormula(),/VALUE\(/);
  assert.match(sheet.getRange('B28').getFormula(),/VALUE\(/);
  sheet.getRange(3,96).setValue('corrupt-base64');
  assert.throws(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss),/VERIFY/);
});

test('configured performance bands produce the exact trusted conditional rules', () => {
  const env=fakeGoogle(),fixture=resultsPresentation();
  fixture.layout.bandStyles=[
    {label:'Needs review',background:'#DC2626',font:'#FFFFFF'},
    {label:'Strong',background:'#16A34A',font:'#FFFFFF'},
  ];
  const descriptor={name:'Results',finalTitle:'Results',presentation:'results-v1',version:1,rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,'3'.repeat(32));
  const op={kind:'layout',version:1,tab:'Results',presentation:'results-v1',layout:fixture.layout};
  receiver.applyPresentationLayout(op,state,env.ss);
  const rules=env.ss.getSheetById(state.tabs[0].id).getConditionalFormatRules();
  assert.deepEqual(rules.map(rule=>rule.text),['Complete','Incomplete','Needs review','Strong']);
  assert.doesNotThrow(()=>receiver.verifyPresentationLayout({...op,kind:'verifyLayout'},state,env.ss));
});

function managedProfileEnvironment() {
  const env=fakeGoogle(),fixture=profilePresentation(),runId='f'.repeat(32);
  const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v1',version:1,
    rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,runId);
  receiver.applyPresentationLayout({kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v1',layout:fixture.layout},state,env.ss);
  const sheet=env.ss.getSheetById(state.tabs[0].id);
  sheet.title='Recruit Profiles';
  env.publish(runId);
  return {env,fixture,sheet,runId};
}

function managedProfileEnvironmentV2() {
  const env=fakeGoogle(),fixture=profilePresentationV2(),runId='a'.repeat(32);
  const descriptor={name:'Recruit Profiles',finalTitle:'Recruit Profiles',presentation:'recruit-profiles-v2',version:1,
    rows:fixture.rows.length,cols:fixture.rows[0].length};
  const state=presentationState(env,descriptor,fixture.rows,runId);
  receiver.applyPresentationLayout({kind:'layout',version:1,tab:'Recruit Profiles',presentation:'recruit-profiles-v2',layout:fixture.layout},state,env.ss);
  const sheet=env.ss.getSheetById(state.tabs[0].id);
  sheet.title='Recruit Profiles';
  env.publish(runId);
  return {env,fixture,sheet,runId};
}

test('v2 scope edits map display labels to stable ids before resetting the profile', () => {
  const {env,sheet}=managedProfileEnvironmentV2();
  env.rejectTransientInvalid(true);
  sheet.getRange('B3').setValue('Day');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  assert.equal(sheet.getRange('E3').getValue(),'Day Alex');
  assert.ok(sheet.getRange('E3').getDataValidation());
  sheet.getRange('B3').setValue('Empty');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  assert.equal(sheet.getRange('E3').getValue(),'');
  assert.equal(sheet.getImages().length,0);
});

test('profile selector trigger ignores every unowned or out-of-scope edit', () => {
  const {env,sheet}=managedProfileEnvironment();
  const original=sheet.getImages()[0];
  receiver.profileSelectionChanged({source:{getId:()=> 'wrong'},range:sheet.getRange('B3')});
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('A1')});
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3:C3')});
  sheet.title='_stage_unpublished_Recruit Profiles';
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  sheet.title='Recruit Profiles';
  env.publish('0'.repeat(32));
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  assert.equal(sheet.getImages()[0],original);
  assert.equal(sheet.getRange('E3').getValue(),'Alex');
});

test('scope edits reset the recruit before refreshing and stable keys select duplicate-name photos', () => {
  const {env,fixture,sheet,runId}=managedProfileEnvironment();
  const originalImage=sheet.getImages()[0];
  sheet.getRange('B3').setValue('Day');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  assert.equal(sheet.getRange('E3').getValue(),'Day Alex');
  assert.ok(sheet.getRange('E3').getDataValidation());
  sheet.getRange('B3').setValue('All completed Journees');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  sheet.getRange('E3').setValue('Alex — Other · 2');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('E3')});
  assert.notEqual(sheet.getImages()[0],originalImage);
  assert.equal(sheet.getImages().length,1);
  assert.equal(sheet.getImages()[0].getAnchorCell().getColumn(),10);
  assert.deepEqual(sheet.getImages()[0].offsetHistory,[1,0]);
  assert.equal(digest(Buffer.from(sheet.getImages()[0].blob.getBytes())),fixture.secondDigest);

  const lastGood=sheet.getImages()[0];
  sheet.getRange(3,96).setValue('corrupt-base64');
  assert.throws(()=>receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('E3')}),/VERIFY/);
  assert.equal(sheet.getImages()[0],lastGood);
  env.publish(runId);
});

test('switching to an empty profile scope clears validation, selection, and the previous image', () => {
  const {env,sheet}=managedProfileEnvironment();
  assert.equal(sheet.getImages().length,1);
  sheet.getRange('B3').setValue('Empty');
  receiver.profileSelectionChanged({source:env.ss,range:sheet.getRange('B3')});
  assert.equal(sheet.getRange('E3').getValue(),'');
  assert.equal(sheet.getRange('E3').getDataValidation(),null);
  assert.equal(sheet.getImages().length,0);
});

test('interactive profile trigger installation is idempotent and scoped to the fixed spreadsheet', () => {
  const env=fakeGoogle();
  receiver.installInteractiveProfileTrigger();
  receiver.installInteractiveProfileTrigger();
  assert.equal(env.triggers().length,1);
  assert.equal(env.triggers()[0].getHandlerFunction(),'profileSelectionChanged');
  assert.equal(env.triggers()[0].getTriggerSourceId(),env.ss.getId());
});

module.exports={fakeGoogle};
