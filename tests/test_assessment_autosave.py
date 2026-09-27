"""Run the shipped autosave controller against delayed acknowledgements."""
from pathlib import Path
import subprocess


def test_autosave_acknowledgements_conflicts_retry_and_disposal():
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8').replace('export function', 'function');
const factory = vm.runInNewContext(source + ';assessmentAutosave', {setTimeout, clearTimeout, JSON});
let current = {values:{factor:0.5},comment:'',notes:''}, calls = [], pending = [], labels = [], conflicts = [];
const controller = factory({read:()=>JSON.parse(JSON.stringify(current)), version:2, delay:5,
  write:body=>{calls.push(body);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
  status:(label)=>labels.push(label), onConflict:value=>conflicts.push(value)});
const drain = () => new Promise(resolve=>setImmediate(resolve));
async function run() {
  await controller.save(); await controller.save();
  assert.equal(calls.length,0,'Untouched focusout/online must not write');
  current.notes='first'; const first=controller.save();
  assert.equal(calls[0].base_version,2);
  current.notes='second'; controller.schedule();
  pending.shift().resolve({version:3}); await drain();
  assert.equal(calls.length,2); assert.equal(calls[1].notes,'second');
  assert.equal(calls[1].base_version,3);
  assert.notEqual(labels.at(-1),'Saved','New edit has not been acknowledged');
  // Returning to an older value while saving still requires a third write.
  current.notes='first'; pending.shift().resolve({version:4}); await drain();
  assert.equal(calls.length,3);assert.equal(calls[2].notes,'first');
  pending.shift().resolve({version:5}); await first;
  assert.equal(labels.at(-1),'Saved'); assert.equal(controller.dirty(),false);
  await controller.save(); assert.equal(calls.length,3);
  current.notes='offline';const offline=controller.save();pending.shift().reject(new Error('offline'));
  assert.equal(await offline,false);assert.equal(controller.dirty(),true);
  const retry=controller.save();pending.shift().resolve({version:6});await retry;
  current.comment='conflicting';const failed=controller.save();pending.shift().reject({status:409});
  assert.equal(await failed,false);await controller.save();assert.equal(calls.length,6);
  assert.equal(labels.at(-1),'Conflict');
  const overwrite=controller.save(10);assert.equal(calls.at(-1).base_version,10);
  pending.shift().resolve({version:11});await overwrite;assert.equal(controller.dirty(),false);
  current.notes='dispose';controller.schedule();controller.dispose();
  await new Promise(resolve=>setTimeout(resolve,15));
  await controller.save();assert.equal(calls.length,7,'Disposed timers and listeners cannot write');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(["node", "-e", script, str(Path(__file__).parents[1]/"app/static/assessment-autosave.js")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
