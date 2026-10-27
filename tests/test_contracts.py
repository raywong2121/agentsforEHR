"""Seventeen E91 machine-contract controls using minimized synthetic fixtures.

Adapted from the independent historical suite; no old runtime, raw model reply,
clinical gold standard, or remote service is required.
"""
import argparse,copy,hashlib,json,sys,tempfile,subprocess,unittest
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
SOURCE_KEYS=['source','units','inventory','source_checks','source_notes','revisions','extraction_notes','inventory_complete','model_inventory_complete','extraction_done','unmodeled']
def read(p):return json.loads(p.read_text())
def dump(v):return json.dumps(v,ensure_ascii=False)
def state(v):return json.loads(v['state_json'])
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--runtime',type=Path,default=ROOT/'src/term_runtime.py');ap.add_argument('--output',type=Path,default=ROOT/'artifacts/contracts.json');a=ap.parse_args();raw=a.runtime.read_bytes();runtimeSHA=hashlib.sha256(raw).hexdigest();ns={'__name__':'independent_e9_controls'}
 for f in (ROOT/'src/rule_json.py',a.runtime):exec(compile(f.read_text(),str(f),'exec'),ns)
 quoted=False;rows=[]
 def expect(ok,msg):
  if not ok:raise AssertionError(msg)
 def run(name,fn):
  try:rows.append({'control':name,'passed':True,'detail':fn()})
  except Exception as e:rows.append({'control':name,'passed':False,'error_type':type(e).__name__,'error':str(e)})
 def initial():return read(ROOT/'fixtures/review_ready.json')
 def owner(s,ref):return ns['binding_owner'](s,ref)
 def bindings(s):return [b for m in s['mappings'] for b in m['bindings']]
 def source(s):return {k:s[k] for k in SOURCE_KEYS}
 def apply(s,d):return state(ns['apply_review'](dump(s),dump(d)))
 def labels(s):
  for b in bindings(s):b['relation']='label_association';b['proposed_relation']='label_association'
  return s
 actualSource={}
 def registry(s):
  view=ns['view_for_call'](s);return view,ns['review_evidence_registry'](s,view)
 def sourceEvidence(s,b):
  lines={sp['line_no'] for t in s['tasks'] if t['target_ref'] in b['covers'] for sp in ns['units_by_id'](s)[t['unit_id']]['spans']}
  return [{'line_no':r['line_no'],'quote':r['text']} for r in s['source']['records'] if r['line_no'] in lines]
 def basis(ref,row,purpose=None):return ({'purpose':purpose} if purpose else {})|{'evidence_ref':ref}|({'quote':row['text']} if quoted else {})
 def positive(s,source_variant='full'):
  v,reg=registry(s);checks=[];assertions=[]
  for wanted in s['call']['targets']:
   m,b=owner(s,wanted['target_ref']);ref,row=next((ref,row) for ref,row in reg.items() if row['kind']=='candidate_label' and row['candidate_ref']==b['candidate_ref']);assessment={'association':'supported','forward':'supported' if b['relation']=='equivalent' else 'not_claimed','reverse':'supported' if b['relation']=='equivalent' else 'not_claimed'}
   r={'target_ref':b['binding_ref'],'revision':b['revision'],'candidate_ref':b['candidate_ref'],'assessment':assessment,'basis':[basis(ref,row,p) for p in assessment if assessment[p]=='supported'],'note':'Synthetic '+source_variant+' mechanism fixture, not semantic gold.'}
   if quoted:r.update(candidate_quote=row['text'],source_evidence=copy.deepcopy(actualSource.get(b['binding_ref'])) if source_variant=='actual_short' and isinstance(actualSource.get(b['binding_ref']),list) else sourceEvidence(s,b))
   checks.append(r)
  for ob in v['assertion_obligations']:
   m,b=owner(s,ob['binding_ref']);ref,row=next((ref,row) for ref,row in reg.items() if row['kind']=='candidate_label' and row['candidate_ref']==b['candidate_ref']);assertions.append({'assertion_ref':ob['assertion_ref'],'verdict':'supported','basis':[basis(ref,row)],'note':'Synthetic fixed-predicate support fixture; no formal Eq proof.'})
  return {'checks':checks,'assertion_checks':assertions,'issues':[]}
 def setcall(s,refs):s['call']={'mode':'mapping_delta','targets':[{'target_ref':r,'revision':owner(s,r)[1]['revision']} for r in refs]};s['stop']=False
 def calibration(s,ref=None):return [i for i in s['issues'] if i.get('calibration_only') and (ref is None or i['target_ref']==ref)]
 def current(s):return [b for b in bindings(s) if b.get('status') in ns['ACCEPTED_BINDING_STATUSES']]
 def same_gap():
  s=initial();refs=[b['binding_ref'] for b in bindings(s)];first=apply(s,positive(s,'actual_short'));setcall(first,refs);second=apply(first,positive(first,'full'));groups={}
  for i in calibration(second):groups.setdefault((i['target_ref'],i['expected_revision']),[]).append(i)
  expect(len(calibration(second))==len(refs) and all(len(v)==1 for v in groups.values()),'Same fixture binding/gap split when valid source excerpt changed')
  chosen=refs[0];setcall(second,[chosen]);third=apply(second,positive(second));expect(len(calibration(third))==len(refs),'Narrower review call/neighbor scope multiplied same issue')
  expect(source(third)==source(s),'Stable-key update changed source11');return {'same_gap_one_issue_across_quote_and_call_scope':True}
 run('K01_reply_note_and_neighbor_scope_do_not_split_business_gap',same_gap)
 def revision():
  s=initial();o=apply(s,positive(s));ref=bindings(s)[0]['binding_ref'];owner(o,ref)[1]['revision']+=1;setcall(o,[ref]);again=apply(o,positive(o));revs={i['expected_revision'] for i in calibration(again,ref)};expect(revs=={1,2},'New revision merged into stale gap');return {'both_revisions_retained':True}
 run('K02_new_binding_revision_is_not_merged_with_old_gap',revision)
 def distinct_predicates():
  s=initial();o=apply(s,positive(s));ref=bindings(s)[0]['binding_ref'];setcall(o,[ref]);d=positive(o);d['checks'][0]['assessment']['association']='unsupported';after=apply(o,d)
  expect(owner(after,ref)[1]['status']=='invalidated','Association negative did not hard invalidate');expect(any(i['target_ref']==ref and i.get('calibration_only') for i in after['issues']),'Distinct Eq gap was overwritten by association negative');expect(any(i['target_ref']==ref and not i.get('calibration_only') and i['required_action']=='replace_mapping' for i in after['issues']),'Association negative was absorbed into Eq calibration');return {'association_hard_and_Eq_gap_separate':True}
 run('K03_genuine_association_negative_is_not_merged_into_Eq_calibration',distinct_predicates)
 def simultaneous_direction():
  s=initial();d=positive(s);ref=d['checks'][0]['target_ref'];d['checks'][0]['assessment']['forward']='not_checkable';obs={r['assertion_ref']:r for r in registry(s)[0]['assertion_obligations']}
  for r in d['assertion_checks']:
   if obs[r['assertion_ref']]['binding_ref']==ref and obs[r['assertion_ref']]['predicate']=='source_equivalence':r['verdict']='not_checkable'
  o=apply(s,d);issues=calibration(o,ref);expect(len(issues)==1,'Raw/typed direction negative and Code formal-proof gap generated duplicate same-predicate calibration tasks');expect(issues[0].get('strict_direction_negative') is True,'Merged issue erased actual direction negative');expect(issues[0]['required_action']=='calibrate_relation','Merged direction negative became soft format retry');return {'one_calibration_task_preserves_native_direction_negative':True}
 run('K04_simultaneous_direction_negative_and_formal_Eq_gap_one_task',simultaneous_direction)
 def attempts():
  s=initial();o=apply(s,positive(s));ref=bindings(s)[0]['binding_ref'];i=calibration(o,ref)[0];i['attempts']=1;before=i['issue_ref'];setcall(o,[ref]);o['last_feedback']={'transient':'test-only'};o['trace'].append({'transient':'counter'});after=apply(o,positive(o));same=calibration(after,ref);expect(len(same)==1 and same[0]['issue_ref']==before and same[0]['attempts']==1,'Same feedback reset attempts or multiplied issue');return {'same_gap_attempt_limit_retained':True}
 run('K05_repeated_feedback_does_not_reset_attempts_or_create_work',attempts)
 def relevant_generation():
  s=initial();o=apply(s,positive(s));ref=bindings(s)[0]['binding_ref'];gap=calibration(o,ref)[0];gap['attempts']=1;gap['status']='model_checked_repair_pending_offline';identity=gap['issue_ref'];setcall(o,[ref]);candidate=owner(o,ref)[1]['candidate_ref'];mr=ns['meaning_refs'](o,candidate)[0];o['meaning_evidence'][mr]['verification_date']='2099-01-01T00:00:00Z';o['meaning_evidence'][mr]['receipt_ref']='synthetic-receipt-only-change';same=apply(o,positive(o));old=calibration(same,ref);expect(len(old)==1 and old[0]['issue_ref']==identity and old[0]['attempts']==1,'Receipt/date-only changes reset business attempts or split key')
  setcall(same,[ref]);newterm=copy.deepcopy(same['meaning_evidence'][mr]['synonyms'][0]);newterm['term']='Synthetic new authoritative meaning term for generation control';same['meaning_evidence'][mr]['synonyms'].append(newterm);changed=apply(same,positive(same));new=calibration(changed,ref);expect(len(new)==1 and new[0]['issue_ref']==identity and new[0]['attempts']==0 and new[0].get('new_actual_evidence_reopened'),'Real relevant meaning content did not reopen same gap under new evidence generation');return {'synthetic_generation_fixture':True,'receipt_only_does_not_reset':True,'relevant_meaning_reopens_same_business_key':True}
 run('K06_only_relevant_new_evidence_reopens_existing_gap',relevant_generation)
 def ref_cross_binding():
  s=labels(initial());d=positive(s);v,reg=registry(s);foreign=next((ref,row) for ref,row in reg.items() if row['kind']=='candidate_label' and row['candidate_ref']==bindings(s)[1]['candidate_ref']);d['checks'][0]['basis'][0]=basis(*foreign,'association');o=apply(s,d);expect(ns['completion_blockers'](o),'Foreign candidate ref certified current binding');expect(owner(o,d['checks'][0]['target_ref'])[1]['status'] not in ns['ACCEPTED_BINDING_STATUSES'],'Cross-binding basis was accepted');return {'foreign_binding_reference_rejected':True}
 run('R01_foreign_binding_candidate_ref_cannot_support_current_binding',ref_cross_binding)
 def ref_newversion():
  s=labels(initial());d=positive(s);ref=d['checks'][0]['target_ref'];candidate=owner(s,ref)[1]['candidate_ref'];old=d['checks'][0]['basis'][0]['evidence_ref'];cid=s['candidate_refs'][candidate];s['catalog'][str(cid)]['vocabulary_version']=str(s['catalog'][str(cid)].get('vocabulary_version'))+'-new-audit-version';new=positive(s);new['checks'][0]['basis'][0]=copy.deepcopy(d['checks'][0]['basis'][0]);o=apply(s,new);expect(owner(o,ref)[1]['status'] not in ns['ACCEPTED_BINDING_STATUSES'],'Old-version basis ref remained valid after vocabulary version changed');expect(ns['completion_blockers'](o),'Old-version ref silently completed');return {'old_vocabulary_reference_rejected':True,'synthetic_row_version_fixture':True}
 run('R02_old_vocabulary_version_reference_cannot_be_reused',ref_newversion)
 def source_version():
  s=labels(initial());d=positive(s);ref=d['checks'][0]['target_ref'];s['source']['source_sha256']='a'*64;new=positive(s);new['checks'][0]['basis'][0]=copy.deepcopy(d['checks'][0]['basis'][0]);o=apply(s,new);expect(owner(o,ref)[1]['status'] not in ns['ACCEPTED_BINDING_STATUSES'],'Old-source-snapshot ref reused current alias');return {'old_source_snapshot_reference_rejected':True,'synthetic_snapshot_identity_fixture':True}
 run('R03_old_source_snapshot_reference_cannot_alias_current_row',source_version)
 def negative_bad_ref():
  s=labels(initial());d=positive(s);ref=d['checks'][0]['target_ref'];d['checks'][0]['assessment']['association']='unsupported';d['checks'][0]['basis'][0]['evidence_ref']='invented_ref';o=apply(s,d);expect(owner(o,ref)[1]['status']=='invalidated','Bad basis ref washed legal current negative into format retry');expect(any(i['target_ref']==ref and i['required_action']=='replace_mapping' and not i.get('calibration_only') for i in o['issues']),'Missing hard negative task');return {'invalid_basis_cannot_hide_native_negative':True}
 run('R04_legal_identity_negative_survives_bad_basis_reference',negative_bad_ref)
 def invalid_identity():
  s=labels(initial());d=positive(s);ref=d['checks'][0]['target_ref'];d['checks'][0]['candidate_ref']='invented';d['checks'][0]['assessment']['association']='unsupported';o=apply(s,d);expect(owner(o,ref)[1]['status']!='invalidated','Invalid candidate identity fabricated semantic negative');expect(not any(i['kind'] in ns['MAPPING_KINDS'] for i in o['issues']),'Illegal identity fabricated medical issue');return {'invalid_identity_contract_only':True}
 run('R05_invalid_candidate_identity_does_not_fabricate_medical_negative',invalid_identity)
 def one_binding_contract_retry():
  s=labels(initial());d=positive(s);ref=d['checks'][0]['target_ref'];d['checks']=d['checks'][1:];after=apply(s,d);neighbors={b['binding_ref'] for b in bindings(s)}-{ref}
  expect(all(owner(after,r)[1]['status'] in ns['ACCEPTED_BINDING_STATUSES'] and after['mapping_checks'][r]['model_checked_current_revision'] for r in neighbors),'One omitted contract row discarded accepted current neighbors')
  nxt=state(ns['model_view'](dump(after)));wanted={r['target_ref'] for r in (nxt.get('call') or {}).get('targets',[])};expect(nxt.get('call',{}).get('mode')=='mapping_delta' and wanted=={ref},'One omitted binding contract row caused whole-parent or neighbor retry');expect(nxt['usage']['loop_waves']==5 and nxt['budget']==s['budget'],'Contract retry reset original wave budget');fixed=apply(nxt,positive(nxt));expect(all(owner(fixed,r)[1]['status'] in ns['ACCEPTED_BINDING_STATUSES'] for r in neighbors|{ref}),'Legal one-binding contract correction lost accepted neighbors');return {'only_missing_binding_retried':True,'synthetic_contract_recovery_not_semantic_repair':True,'neighbor_count':len(neighbors)}
 run('R06_only_failed_binding_contract_is_retried_accepted_neighbors_retained',one_binding_contract_retry)
 partialValue=read(ROOT/'fixtures/partial_selection.json');partialRaw=dump(partialValue);partialTargets={ref for row in partialValue['selections'] for ref in row['target_refs']}
 def partial_stage():
  s=initial();o=apply(s,positive(s));v5=state(ns['model_view'](dump(o)));expect(v5['call']['mode']=='select_only' and v5['usage']['loop_waves']==5,'No fifth-wave Planner dispatch');allTargets=set(v5['call']['target_refs']);p=state(ns['plan'](dump(v5),partialRaw));expect(p.get('call',{}).get('mode')=='mapping_delta','Legal fixture partial response did not get current later review');reviewTargets={r['target_ref'] for r in p['call']['targets']};reviewCovers={ref for bref in reviewTargets for ref in owner(p,bref)[1]['covers']};expect(reviewCovers==partialTargets,'Unchanged accepted neighbors unnecessarily re-reviewed');done=apply(p,positive(p));remaining=allTargets-partialTargets;nexts=state(ns['model_view'](dump(done)));return s,done,nexts,remaining
 def queue_only_missing():
  s,done,nexts,remaining=partial_stage();expect(nexts.get('call',{}).get('mode')=='select_only' and set(nexts['call']['target_refs'])==remaining,'Next queue did not request only omitted undecided targets');expect(nexts['usage']['loop_waves']==6 and nexts['budget']==s['budget'],'Partial repair reset original budget');expect(len(current(done))==len(bindings(s)),'Legal fixture partial response lost accepted neighbors or useful current labels');expect(source(nexts)==source(s),'Queue changed source11');return {'fixture_partial_targets':sorted(partialTargets),'next_only_omitted_targets':sorted(remaining),'new_model_calls':0}
 run('Q01_fixture_partial_accepts_neighbors_and_next_only_omitted',queue_only_missing)
 def complete_partial():
  s,done,nexts,remaining=partial_stage();expect(nexts.get('call',{}).get('mode')=='select_only','No remaining task queue');selections=[]
  for ref in nexts['call']['target_refs']:
   m,b=next((m,b) for m in nexts['mappings'] for b in m['bindings'] if b['status']!='invalidated' and ref in b['covers']);new={k:copy.deepcopy(b[k]) for k in ('candidate_ref','role','relation','relation_origin','relationship_ref','covers')};new.update(relation='label_association',relation_origin='model_semantic_judgment',relationship_ref=None);selections.append({'target_refs':copy.deepcopy(b['covers']),'bindings':[new],'dependencies':[],'replaces':[b['binding_ref']],'residuals':[],'reason':'Synthetic remaining-target calibration; not an actual model repair.'})
  planned=state(ns['plan'](dump(nexts),dump({'selections':selections,'unresolved':[],'meaning_requests':[]})));expect(planned.get('call',{}).get('mode')=='mapping_delta','New selections skipped mandatory later review');finished=apply(planned,positive(planned));expect(not ns['completion_blockers'](finished),'Later valid remaining-target engineering review left false blockers');expect(len(current(finished))==len(bindings(s)) and source(finished)==source(s),'Partial continuation lost source or codes');expect(finished['usage']['loop_waves']==6,'Continuation invented budget');return {'synthetic_later_Planner_and_Reviewer':True,'actual_semantic_repairs':0}
 run('Q02_remaining_queue_then_later_review_closes_engineering_fixture',complete_partial)
 def no_seventh():
  s=initial();s['call']=None;s['usage']['loop_waves']=6;o=state(ns['model_view'](dump(s)));expect(o['stop'] and not o.get('call') and o['usage']['loop_waves']==6,'Exhausted six-wave state got invented extra wave');return {'budget_exhaustion_not_relabelled_repaired':True}
 run('Q03_budget_exhaustion_never_gets_seventh_wave',no_seventh)
 def strict_not_lowered():
  s=initial();o=apply(s,positive(s));expect(not any(b.get('strict_semantic_equivalence_verified') or b.get('status')=='proposed_equivalent' for b in bindings(o)),'Ref-only contract weakened strict equivalence');return {'strict_Eq_not_proved_by_refs_labels_or_votes':True}
 run('S01_reference_reduction_does_not_weaken_strict_Eq',strict_not_lowered)
 run('S02_runtime_remained_frozen_through_suite',lambda:expect(hashlib.sha256(a.runtime.read_bytes()).hexdigest()==runtimeSHA,'Runtime changed during audit'))
 report={'scope':'Offline E91 controls using minimized TEST-vocabulary derivative fixtures; synthetic replies are engineering only; not an exact archival or clinical replay','runtime_file':'src/'+a.runtime.name,'runtime_sha256':runtimeSHA,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'uses_legacy_quoted_contract':quoted,'passed':all(r['passed'] for r in rows),'passed_count':sum(r['passed'] for r in rows),'failed_count':sum(not r['passed'] for r in rows),'controls':rows,'new_model_calls':0,'actual_semantic_repairs':0}
 a.output.parent.mkdir(parents=True,exist_ok=True)
 with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
 print(json.dumps({'passed':report['passed'],'passed_count':report['passed_count'],'failed_count':report['failed_count'],'failures':[r for r in rows if not r['passed']]},ensure_ascii=False));return 0 if report['passed'] else 1

CONTROL_NAMES = [
    'K01_reply_note_and_neighbor_scope_do_not_split_business_gap',
    'K02_new_binding_revision_is_not_merged_with_old_gap',
    'K03_genuine_association_negative_is_not_merged_into_Eq_calibration',
    'K04_simultaneous_direction_negative_and_formal_Eq_gap_one_task',
    'K05_repeated_feedback_does_not_reset_attempts_or_create_work',
    'K06_only_relevant_new_evidence_reopens_existing_gap',
    'R01_foreign_binding_candidate_ref_cannot_support_current_binding',
    'R02_old_vocabulary_version_reference_cannot_be_reused',
    'R03_old_source_snapshot_reference_cannot_alias_current_row',
    'R04_legal_identity_negative_survives_bad_basis_reference',
    'R05_invalid_candidate_identity_does_not_fabricate_medical_negative',
    'R06_only_failed_binding_contract_is_retried_accepted_neighbors_retained',
    'Q01_fixture_partial_accepts_neighbors_and_next_only_omitted',
    'Q02_remaining_queue_then_later_review_closes_engineering_fixture',
    'Q03_budget_exhaustion_never_gets_seventh_wave',
    'S01_reference_reduction_does_not_weaken_strict_Eq',
    'S02_runtime_remained_frozen_through_suite',
]
class ContractControls(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  with tempfile.TemporaryDirectory(prefix='ohdsi-contract-tests-') as tmp:
   path=Path(tmp)/'controls.json'
   process=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--output',str(path)],capture_output=True,text=True)
   if not path.exists():raise AssertionError(process.stderr or process.stdout)
   cls.report=json.loads(path.read_text())
   cls.controls={r['control']:r for r in cls.report['controls']}
   if set(cls.controls)!=set(CONTROL_NAMES):raise AssertionError('Declared controls differ from executed controls')

def _make_control(name):
 def test(self):
  row=self.controls[name]
  self.assertTrue(row['passed'],row)
 return test
for _name in CONTROL_NAMES:setattr(ContractControls,'test_'+_name,_make_control(_name))

if __name__=='__main__':raise SystemExit(main())
