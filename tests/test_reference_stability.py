"""Fresh-interpreter regression of canonical evidence references; no services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import copy,hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);runtime=Path(sys.argv[2]);ns={'__name__':'reference_process_probe'}
for path in (root/'src/rule_json.py',runtime):exec(compile(path.read_text(),str(path),'exec'),ns)
state=json.loads((root/'fixtures/cross_process.json').read_text())
first=ns['view_for_call'](copy.deepcopy(state));second=ns['view_for_call'](json.loads(ns['dump'](state)))
canon=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
signature=lambda view:{'snapshot':view['evidence_snapshot_sha256'],'scope':view['evidence_scope_sha256'],'refs':sorted(view['evidence_rows']),'canonical_rows_sha256':hashlib.sha256(canon(sorted(view['evidence_rows'].values(),key=canon)).encode()).hexdigest()}
# Fixture-only mutation reproduces E9's insertion-order-sensitive registry hashing.
# No old runtime is shipped or silently substituted for the current runtime.
ns['content_sha']=ns['sha']
legacy=ns['view_for_call'](copy.deepcopy(state))
print(json.dumps({'first':signature(first),'serialized_roundtrip':signature(second),'legacy_hash_mutation':signature(legacy)}))
'''

def check_reference_stability(runtime=None):
    runtime = Path(runtime or ROOT/'src/term_runtime.py').resolve()
    before = hashlib.sha256(runtime.read_bytes()).hexdigest()
    runs = []
    for seed in (1,2,3,4,5,17,41,99):
        env = dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.run([sys.executable, '-c', PROBE, str(ROOT), str(runtime)], env=env,
                              capture_output=True, text=True, check=True)
        runs.append({'PYTHONHASHSEED':seed, **json.loads(proc.stdout)})
    canonical = {r['first']['canonical_rows_sha256'] for r in runs}
    snapshots = {r['first']['snapshot'] for r in runs}
    refs = {tuple(r['first']['refs']) for r in runs}
    scopes = {r['first']['scope'] for r in runs}
    legacy = {r['legacy_hash_mutation']['snapshot'] for r in runs}
    checks = {
        'all_processes_observe_same_fixture_evidence':len(canonical)==1,
        'same_content_snapshot_stable_across_processes':len(snapshots)==1,
        'same_content_refs_stable_across_processes':len(refs)==1,
        'same_content_scope_digest_stable_across_processes':len(scopes)==1,
        'same_process_serialized_roundtrip_stable':all(r['first']==r['serialized_roundtrip'] for r in runs),
        'runtime_unchanged':hashlib.sha256(runtime.read_bytes()).hexdigest()==before,
        'legacy_hash_mutation_reproduces_order_sensitivity':len(legacy)>1,
    }
    return {'scope':'Offline minimized synthetic fixture; current-runtime regression and historical hash mutation control, not an exact historical replay or semantic acceptance',
            'passed':all(checks.values()),'checks':checks,'runtime_file':'src/'+runtime.name,
            'runtime_sha256':before,'fixture_sha256':hashlib.sha256((ROOT/'fixtures/cross_process.json').read_bytes()).hexdigest(),
            'processes':len(runs),'distinct_snapshots':len(snapshots),'distinct_ref_sets':len(refs),
            'distinct_legacy_snapshots':len(legacy),'runs':runs,'new_model_calls':0,
            'actual_semantic_repairs':0,'services_called':0}

class ReferenceStabilityTests(unittest.TestCase):
    def test_fresh_interpreters_and_historical_hash_mutation(self):
        result = check_reference_stability()
        type(self).report = result
        self.assertTrue(result['passed'], {k:v for k,v in result['checks'].items() if not v})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,default=ROOT/'src/term_runtime.py')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/references.json')
    args=parser.parse_args(); result=check_reference_stability(args.runtime)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:result[k] for k in ('passed','processes','distinct_snapshots','distinct_ref_sets','distinct_legacy_snapshots')}))
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
