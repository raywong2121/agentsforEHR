"""Run the offline suite and write a non-overwriting, credential-free report."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import time
import unittest
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/offline-tests.json')
    args=parser.parse_args()
    # Refuse an overwrite before running; every attempt can be retained separately.
    if args.output.exists():parser.error('output already exists; choose a new filename to retain the prior result')
    stream=io.StringIO();started=time.perf_counter()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    elapsed=time.perf_counter()-started
    contracts=getattr(sys.modules.get('test_contracts').ContractControls,'report',None)
    references=getattr(sys.modules.get('test_reference_stability').ReferenceStabilityTests,'report',None)
    report={'scope':'Local standard-library engineering regression; no Dify integration, model call or clinical semantic acceptance',
            'python_version':platform.python_version(),'passed':result.wasSuccessful(),
            'tests_run':result.testsRun,'failure_count':len(result.failures),'error_count':len(result.errors),
            'elapsed_seconds':round(elapsed,6),'new_model_calls':0,'services_called':0,'actual_semantic_repairs':0,
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'src/term_runtime.py',ROOT/'src/rule_json.py')},
            'fixture_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'fixtures').glob('*.json'))},
            'contracts':contracts,'reference_stability':references,'unittest_log':stream.getvalue()}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as output:json.dump(report,output,ensure_ascii=False,indent=2);output.write('\n')
    print(json.dumps({k:report[k] for k in ('passed','tests_run','failure_count','error_count','elapsed_seconds','new_model_calls')}))
    if not result.wasSuccessful():print(stream.getvalue(),file=sys.stderr)
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
