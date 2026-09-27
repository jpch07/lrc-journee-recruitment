"""Exercise shipped in-flight deduplication and stale activity guards."""
from pathlib import Path
import subprocess


def test_activity_loading_deduplicates_and_rejects_stale_or_dirty_responses():
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const code=source.slice(source.indexOf('let assignmentLoadSequence'),source.indexOf('  state.activityOperation = operation;'))+'return bundle;};renderAssignmentsV2';
const pending=[],urls=[];
const state={journey:{id:'alpha'},section:'assignments',assignmentActivity:'sport',dirty:false,
 system:{activities:[{key:'sport',assignment:{}},{key:'escape_room',assignment:{}}]}};
const render=vm.runInNewContext(code,{state,Map,configuredActivity:()=>({assignment:{mode:'automatic_groups'}}),
 api:url=>{urls.push(url);return new Promise(resolve=>pending.push(resolve));}});
async function run(){
 const a=render(),b=render();assert.equal(urls.length,1);
 state.assignmentActivity='escape_room';const newer=render();assert.equal(urls.length,2);
 pending[1]({operation:{activityCode:'escape_room'}});assert.equal((await newer).operation.activityCode,'escape_room');
 pending[0]({operation:{activityCode:'sport'}});assert.equal(await a,undefined);assert.equal(await b,undefined);
 const dirty=render();state.dirty=true;pending[2]({operation:{}});assert.equal(await dirty,undefined);
 state.dirty=false;const tenant=render();state.journey={id:'beta'};pending[3]({operation:{}});assert.equal(await tenant,undefined);
 const fresh=render();assert.match(urls.at(-1),/journeys\/beta\//);pending[4]({operation:{}});assert.ok(await fresh);
 assert.equal(urls.length,5,'Completed results must not be cached across requests');
}
run().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script, str(Path(__file__).parents[1]/"app/static/admin.js")],capture_output=True,text=True)
    assert result.returncode == 0, result.stdout+result.stderr


def test_old_room_copy_response_cannot_replace_a_newer_render():
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('  if ($("#editPublishedRooms"))');
const code=source.slice(start,source.indexOf('  if ($("#applyRoomChanges"))',start));
let resolve,rendered=0,calls=0;
const button={disabled:false};
const context={button,$:()=>button,guardDirty:()=>true,canChangeWorkingPlans:()=>true,state:{journey:{id:'alpha'},assignmentActivity:'escape_room',section:'assignments'},sequence:1,assignmentLoadSequence:1,
 journeyId:'alpha',activity:'escape_room',publishedRooms:{id:'plan'},bundle:{},mutation:()=>({}),
 api:()=>{calls++;return new Promise(r=>resolve=r);},renderAssignmentsV2:()=>{rendered++;},toast:()=>{}};
const edit=vm.runInNewContext(code+';button.onclick',context);
async function run(){const old=edit({currentTarget:button});context.assignmentLoadSequence=2;resolve({id:'working'});await old;assert.equal(rendered,0);assert.equal(calls,1);}
run().catch(error=>{console.error(error);process.exitCode=1});
"""
    result=subprocess.run(["node","-e",script,str(Path(__file__).parents[1]/"app/static/admin.js")],capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr


def test_working_editor_saves_preserve_other_draft_and_block_publication():
    script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const code=source.slice(source.indexOf('function markWorkingDirty'),source.indexOf('async function renderMonitoring'));
let calls=0,refreshes=0,resolve,reject,acks=[],messages=[];
const state={assignmentDrafts:{rooms:false,assignments:false},dirty:false,assignmentSaveInFlight:false};
const control={disabled:false,isConnected:true};
const refresh=async()=>{refreshes++};
const context={state,host:{},$$:()=>[control],setDirty:v=>state.dirty=v,toast:message=>messages.push(message),
 renderAssignments:refresh,refreshJourney:async()=>{refreshes++},mutation:()=>({}),
 api:()=>{calls++;return new Promise((r,j)=>{resolve=r;reject=j});}};
const functions=vm.runInNewContext(code+';({markWorkingDirty,saveWorkingEdits,actionAndRefresh,canChangeWorkingPlans})',context);
async function run(){
 functions.markWorkingDirty('rooms');functions.markWorkingDirty('assignments');
 await functions.actionAndRefresh('publish','POST',{},'published',refresh);
 assert.equal(calls,0,'Apply while dirty must not publish stored data');
 let save=functions.saveWorkingEdits('room','PUT',{},'saved',refresh,'rooms',r=>acks.push(r.editRevision));
 assert.equal(control.disabled,true);assert.equal(functions.canChangeWorkingPlans(),false);
 resolve({editRevision:2});await save;
 assert.equal(state.dirty,true);assert.equal(state.assignmentDrafts.rooms,false);
 assert.equal(state.assignmentDrafts.assignments,true);assert.equal(refreshes,0,'Other DOM draft must not rerender');
 assert.equal(control.disabled,false);assert.deepEqual(acks,[2]);
 save=functions.saveWorkingEdits('assignment','PUT',{},'saved',refresh,'assignments',r=>acks.push(r.editRevision));
 reject({message:'version conflict',status:409});await save;
 assert.equal(state.dirty,true);assert.equal(refreshes,0,'Failed save preserves both draft and acknowledged versions');
 save=functions.saveWorkingEdits('assignment','PUT',{},'saved',refresh,'assignments',r=>acks.push(r.editRevision));
 resolve({editRevision:3});await save;assert.equal(state.dirty,false);assert.equal(refreshes,2);assert.deepEqual(acks,[2,3]);
 const apply=functions.actionAndRefresh('publish','POST',{},'published',refresh);resolve({});await apply;
 assert.equal(calls,4);assert.equal(refreshes,4,'Clean Apply retains normal refresh behavior');
 // Reverse the save order; rooms must remain a draft too.
 functions.markWorkingDirty('rooms');functions.markWorkingDirty('assignments');refreshes=0;
 save=functions.saveWorkingEdits('assignment','PUT',{},'saved',refresh,'assignments',()=>{});
 resolve({editRevision:4});await save;assert.equal(state.assignmentDrafts.rooms,true);assert.equal(state.dirty,true);assert.equal(refreshes,0);
}
run().catch(error=>{console.error(error);process.exitCode=1});
"""
    result=subprocess.run(["node","-e",script,str(Path(__file__).parents[1]/"app/static/admin.js")],capture_output=True,text=True)
    assert result.returncode == 0,result.stdout+result.stderr
