"""E1 evidence strategy: one Code-owned mode per call; model checks are not gold."""
import copy
import datetime
import hashlib
import json
import math
import re
if 'recover' not in globals():
    from rule_json import recover
VERSION='evidence_strategy_e1'
LIMITS={'max_loop_waves':6,'max_query_batches':3,'max_terms_per_batch':6,'max_repair_waves':2}
SQL_CONTRACT_MAX_TERMS=8
UNIT_KEYS={'unit_id','kind','label','spans','subject','value','unit','site','attributes','relations'}
CONCEPT_FIELDS=('concept_id','concept_name','vocabulary_id','concept_class_id','domain_id','concept_code','standard_concept','invalid_reason','valid_start_date','valid_end_date')
SOURCE_KINDS={'source_omission','scope_or_context'}
QUERY_KINDS={'candidate_gap','retrieval_incomplete','meaning_evidence_missing'}
MAPPING_KINDS={'mapping_role_or_range','unsupported_specificity'}
RATIONALE_KINDS={'rationale_unsupported'}
ACCEPTED_BINDING_STATUSES={'proposed_equivalent','proposed_label_association'}


def dump(v):
    return json.dumps(v, ensure_ascii=False, separators=(',',':'), allow_nan=False)

def sha(v):
    return hashlib.sha256(dump(v).encode()).hexdigest()

def content_sha(v):
    return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def obj(v):
    return json.loads(v) if isinstance(v,str) else copy.deepcopy(v)

def state_load(v):
    s=obj(v)
    if not isinstance(s,dict) or s.get('schema_version')!=VERSION:
        raise ValueError('state_schema_mismatch')
    return s

def only(v, allowed, required=()):
    if not isinstance(v,dict) or set(v)-set(allowed) or not set(required)<=set(v):
        raise ValueError('object_contract_keys')

class ContractError(ValueError):
    def __init__(self,code,field_path,expected,actual):
        super().__init__(code)
        self.field_path=field_path;self.expected=expected
        self.actual_type='null' if actual is None else type(actual).__name__

def bounded_list(v, maximum=100, field_path=None):
    if not isinstance(v,list) or len(v)>maximum:
        if field_path is not None:raise ContractError('array_contract',field_path,'array; max '+str(maximum)+'; empty=[]',v)
        raise ValueError('array_contract')
    return v

def selection_object(v,allowed,required,field_path):
    try:only(v,allowed,required)
    except ValueError:raise ContractError('object_contract_keys',field_path,'object with required keys '+','.join(sorted(required)),v)

def plan_rejection(error,proposal=None,target_refs=None):
    row={'code':str(error)}
    if proposal is not None:row['proposal']=copy.deepcopy(proposal)
    if target_refs is not None:row['target_refs']=list(target_refs)
    if isinstance(error,ContractError):
        row.update(field_path=error.field_path,expected=error.expected,actual_type=error.actual_type,retryable_contract=True,target_refs=list(target_refs or []))
    return row

def contract_proposal_fingerprint(row):
    public={k:v for k,v in row.items() if k!='reason'} if isinstance(row,dict) else row
    return hashlib.sha256(json.dumps(public,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def selection_targets_for_feedback(row,call):
    proposed=row.get('target_refs') if isinstance(row,dict) else None
    return [r for r in proposed if isinstance(r,str) and r in call['target_refs']] if isinstance(proposed,list) and proposed else list(call['target_refs'])

def decision_items(s,call):
    ledger=s.setdefault('decision_queue',{});items=[]
    for ref in call.get('target_refs',[]):
        task=task_by_ref(s,ref)
        gaps=sorted(i['fingerprint'] for i in s['issues'] if i['issue_ref'] in call.get('issue_refs',[]) and
                    (i['target_ref']==ref or ref in (binding_owner(s,i['target_ref'])[1] or {}).get('covers',[])))
        key=sha({'source':s['source']['source_sha256'],'target_ref':ref,'revision':task['revision'],'value':task['value_sha256'],'gaps':gaps})
        row=ledger.setdefault(key,{'queue_key':key,'source_sha256':s['source']['source_sha256'],'target_ref':ref,'revision':task['revision'],'gap_keys':gaps,'status':'pending','responses':[]})
        items.append(row)
    return items

def settle_selection_targets(s,call,rejected,outcomes=None):
    outcomes=outcomes or {};items={row['target_ref']:row for row in decision_items(s,call)}
    feedback=s.setdefault('selection_contract_feedback',{})
    for ref in call.get('target_refs',[]):
        t=task_by_ref(s,ref);item=items[ref]
        if ref in outcomes:
            for prior in s['decision_queue'].values():
                if prior['target_ref']==ref and prior['revision']==t['revision'] and prior['source_sha256']==s['source']['source_sha256']:
                    prior.update(copy.deepcopy(outcomes[ref]));prior['responses'].append({'wave':s['usage']['loop_waves'],'disposition':prior['status'],'actual_decision_queue_key':item['queue_key']})
            continue
        if t['status']=='awaiting_meaning':
            item['status']='awaiting_meaning';item['responses'].append({'wave':s['usage']['loop_waves'],'disposition':'awaiting_meaning'});continue
        errors=[r for r in rejected if r.get('retryable_contract') and ref in r.get('target_refs',[]) and r.get('code')!='selection_target_not_decided']
        all_errors=[r for r in rejected if ref in r.get('target_refs',[])]
        item['last_rejections']=copy.deepcopy(all_errors)
        if errors:
            feedback[ref]=copy.deepcopy(errors)
            if t.get('selection_contract_retry_attempts',0)<1 and s['usage']['loop_waves']<s['budget']['max_loop_waves']:
                t['status']='contract_retry_pending';item['status']='pending_rejected';t.pop('unresolved_reason',None)
            else:t['status']='unresolved';t['unresolved_reason']='selection_contract_retry_exhausted';item['status']='exhausted_pending'
        else:
            item['status']='pending_rejected' if any(r.get('code')!='selection_target_not_decided' for r in all_errors) else 'pending_omitted'
            t['status']='decision_pending';t.pop('unresolved_reason',None)
        item['responses'].append({'wave':s['usage']['loop_waves'],'disposition':item['status']})

def pointer(v,path):
    if not isinstance(path,str) or not path.startswith('/') or path=='/':
        raise ValueError('invalid_field_pointer')
    cur=v
    for encoded in path[1:].split('/'):
        if re.search(r'~(?![01])',encoded): raise ValueError('invalid_pointer_escape')
        key=encoded.replace('~1','/').replace('~0','~')
        if isinstance(cur,list):
            if not re.fullmatch(r'0|[1-9][0-9]*',key): raise ValueError('invalid_array_pointer')
            cur=cur[int(key)]
        elif isinstance(cur,dict): cur=cur[key]
        else: raise ValueError('pointer_not_found')
    return cur

def units_by_id(s): return {u['unit_id']:u for u in s['units']}

def spans(raw,source):
    rows={r['line_no']:r['text'] for r in source['records']}
    out=[]
    for x in bounded_list(raw,100):
        only(x,{'line_no','quote'},{'line_no'})
        n=x['line_no']
        if type(n) is not int or n not in rows: raise ValueError('source_line_missing')
        if 'quote' in x and x['quote']!=rows[n]: raise ValueError('source_quote_not_exact_full_line')
        out.append({'line_no':n,'quote':rows[n]})
    if not out: raise ValueError('source_spans_required')
    return out

def normalization(u):
    n=u['attributes'].get('normalization')
    if n is None:return
    only(n,{'kind','components','canonical_value','canonical_unit','quote'}, {'kind','components','canonical_value','canonical_unit','quote'})
    if (u['attributes'].get('role'),n.get('kind')) in (('count','duration'),('duration','count')):raise ValueError('conflicting_quantity_role_and_normalization')
    if not isinstance(n['quote'],str) or not n['quote'] or not any(n['quote'] in x['quote'] for x in u['spans']): raise ValueError('normalization_quote_not_in_source')
    if type(n['canonical_value']) not in (int,float) or not math.isfinite(n['canonical_value']): raise ValueError('normalization_number')
    if n['kind'] in ('count','number'):
        if n['canonical_unit']!=n['kind'] or type(u['value']) not in (int,float) or n['canonical_value']!=u['value'] or n['components']!=[]:
            raise ValueError('normalization_count_number_contract')
    elif n['kind']=='duration':
        factors={'second':1,'minute':60,'hour':3600,'day':86400,'week':604800}
        total=0
        for c in bounded_list(n['components'],5):
            only(c,{'value','unit'},{'value','unit'})
            if type(c['value']) not in (int,float) or c['value']<0 or c['unit'] not in factors: raise ValueError('duration_component_contract')
            total+=c['value']*factors[c['unit']]
        if not n['components'] or n['canonical_unit'] not in ('day','second'): raise ValueError('duration_unit_contract')
        expected=total/(86400 if n['canonical_unit']=='day' else 1)
        if not math.isclose(expected,n['canonical_value'],rel_tol=1e-10,abs_tol=1e-10): raise ValueError('duration_arithmetic_mismatch')
    else: raise ValueError('normalization_kind')

def unit(raw,source):
    only(raw,UNIT_KEYS,UNIT_KEYS)
    if not isinstance(raw['unit_id'],str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}',raw['unit_id']): raise ValueError('unit_identity')
    if raw['kind'] not in ('event','attribute','document','relation','other'): raise ValueError('unit_kind')
    if not isinstance(raw['label'],str) or not raw['label'].strip() or len(raw['label'])>240:raise ValueError('unit_label')
    if raw['subject'] is not None and (not isinstance(raw['subject'],str) or not raw['subject'].strip() or len(raw['subject'])>240):raise ValueError('unit_subject')
    for k in ('unit','site'):
        if raw[k] is not None and (not isinstance(raw[k],str) or len(raw[k])>240):raise ValueError('unit_'+k)
    if type(raw['value']) not in (str,int,float,bool,type(None)): raise ValueError('unit_scalar_value')
    if not isinstance(raw['attributes'],dict) or len(dump(raw['attributes']))>8000:raise ValueError('unit_attributes')
    u=copy.deepcopy(raw);u['spans']=spans(raw['spans'],source)
    for r in bounded_list(u['relations'],40):
        only(r,{'predicate','target_unit_id','spans'},{'predicate','target_unit_id','spans'})
        if not isinstance(r['predicate'],str) or not r['predicate'] or not isinstance(r['target_unit_id'],str):raise ValueError('relation_contract')
        r['spans']=spans(r['spans'],source)
    def quotes(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if k=='quote' and (not isinstance(v,str) or not v or not any(v in a['quote'] for a in u['spans'])):raise ValueError('attribute_quote_outside_source')
                quotes(v)
        elif isinstance(x,list):
            for y in x:quotes(y)
    quotes(u['attributes'])
    for scope in bounded_list(u['attributes'].get('modifier_scope',[]),30):
        only(scope,{'quote','target','status'},{'quote','target','status'})
        if scope['status'] not in ('resolved','unresolved'):raise ValueError('modifier_scope_status')
        if scope['status']=='resolved' and scope['target'] is None:raise ValueError('resolved_modifier_scope_requires_target')
        if scope['target'] is not None:only(scope['target'],{'unit_id','field_path'},{'unit_id','field_path'})
    normalization(u)
    return u

def log(s,stage,accepted=None,rejected=None,info=None):
    row={'stage':stage,'wave':s['usage']['loop_waves'],'accepted':accepted or [],'rejected':rejected or [],'info':info or {},'semantic_correctness_verified':False}
    s['trace'].append(row);s['last_feedback']=row


def parse(s,raw,role):
    r=recover(raw if isinstance(raw,str) else dump(raw))
    s['format_audit'].append({'role':role,'ok':r['ok'],'status':r['status'],'reason':r.get('reason'),'audit':r.get('audit'),'semantic_repair_claimed':False})
    if not r['ok']:raise ValueError('bounded_json_recovery_rejected:'+str(r.get('reason')))
    return r['value']


def budget_view(s):
    b=s['budget'];u=s['usage']
    return dict(b,loop_waves_remaining=max(0,b['max_loop_waves']-u['loop_waves']),query_batches_remaining=max(0,b['max_query_batches']-u['query_batches_reserved']),repair_waves_remaining=max(0,b['max_repair_waves']-u['repair_waves']))


def prepare(case_text,question='',strategy_memory_str=''):
    if not isinstance(case_text,str) or not case_text.strip() or len(case_text)>20000:raise ValueError('case_text_trial_range_1_to_20000')
    if not isinstance(question,str) or len(question)>2000:raise ValueError('question_contract')
    records=[];offset=0
    for i,line in enumerate(case_text.splitlines(keepends=True),1):
        text=line.rstrip('\r\n');records.append({'line_no':i,'source_ref':'l'+str(i),'text':text,'char_start':offset,'char_end':offset+len(text)});offset+=len(line)
    source={'case_text':case_text,'records':records,'question':question,'source_sha256':hashlib.sha256(case_text.encode()).hexdigest()}
    budget=copy.deepcopy(LIMITS);warning=None
    if strategy_memory_str:
        try:
            p=obj(strategy_memory_str);only(p,{'max_terms','action_priority','focus_fields'})
            if 'max_terms' in p:
                if type(p['max_terms'])is not int or not 1<=p['max_terms']<=SQL_CONTRACT_MAX_TERMS:raise ValueError('max_terms_contract')
                budget['max_terms_per_batch']=p['max_terms']
            for k in ('action_priority','focus_fields'):
                if k in p and (not isinstance(p[k],list) or any(x not in ('source','core','component','site','quantity','context','mapping') for x in p[k])):raise ValueError('strategy_preferences_only')
        except (ValueError,TypeError,json.JSONDecodeError):warning='strategy_input_rejected_no_facts_imported'
    s={'schema_version':VERSION,'source':source,'units':[],'inventory':[],'source_notes':[],'inventory_complete':False,'revisions':{},'catalog':{},'candidate_refs':{},'relationships':{},'tasks':[],'queries':[],'pending_query':None,'mappings':[],'issues':[],'source_checks':{},'mapping_checks':{},'call':None,'delta_refs':[],'rejected_fingerprints':{},'review_attempts':[], 'budget':budget,'usage':{'loop_waves':0,'query_batches_reserved':0,'repair_waves':0},'stop':False,'stop_reason':None,'trace':[],'last_feedback':None,'format_audit':[],'changes':[],'warning':warning,'extraction_done':False}
    return {'source_json':dump(source),'policy_json':dump({'budget':budget,'warning':warning}),'state_json':dump(s)}


def output(s):return {'state_json':dump(s),'stop':bool(s['stop'])}


def validate_links(rows):
    ids={u['unit_id'] for u in rows}
    for u in rows:
        if any(r['target_unit_id'] not in ids for r in u['relations']):raise ValueError('relation_target_missing')
        for scope in u['attributes'].get('modifier_scope',[]):
            if scope['target'] is not None:
                uid=scope['target']['unit_id'];v=next((x for x in rows if x['unit_id']==uid),None)
                if v is None:raise ValueError('scope_target_missing')
                pointer(v,scope['target']['field_path'])


def unmodeled_row(pending,origin,field_path,reason,raw,unit_ref=None):
    pending.append({'origin':origin,'unit_ref':unit_ref,'field_path':field_path,'reason':reason,'raw':copy.deepcopy(raw),'status':'unmodeled_pending','represented':False})


def root_fields(raw,allowed,required,pending,origin):
    if not isinstance(raw,dict) or not set(required)<=set(raw):raise ValueError('object_contract_keys')
    for key in sorted(set(raw)-set(allowed)):unmodeled_row(pending,origin,'/'+key,'unknown_root_field',raw[key])
    return {k:copy.deepcopy(v) for k,v in raw.items() if k in allowed}


def retain_unmodeled(s,pending):
    saved=s.setdefault('unmodeled',[])
    for row in pending:
        row=copy.deepcopy(row);row['pending_ref']='x'+str(len(saved)+1);saved.append(row)
    return [{'code':x['reason'],'pending_ref':x['pending_ref'],'unit_ref':x['unit_ref'],'field_path':x['field_path']} for x in saved[-len(pending):]] if pending else []


def owned_units(raw,source,prefix,pending=None):
    # Quarantine exact offending payloads; never infer a pointer, subject or field owner.
    pending=[] if pending is None else pending;rows=bounded_list(raw,160);origin='blind_inventory' if prefix=='a' else 'extract'
    ids=[x.get('unit_id') if isinstance(x,dict) else None for x in rows]
    valid={x for x in ids if isinstance(x,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}',x) and ids.count(x)==1}
    remap={x:prefix+str(i+1) for i,x in enumerate(ids) if isinstance(x,str) and x in valid};out=[];links={}
    for i,row in enumerate(rows):
        ref=remap.get(ids[i]) if isinstance(ids[i],str) else None;path='/units/'+str(i)
        try:
            if ref is None:raise ValueError('temporary_unit_keys_unique')
            if not isinstance(row,dict):raise ValueError('object_contract_keys')
            for key in sorted(set(row)-UNIT_KEYS):unmodeled_row(pending,origin,path+'/'+key,'unknown_unit_root_field',row[key],ref)
            u={k:copy.deepcopy(v) for k,v in row.items() if k in UNIT_KEYS};only(u,UNIT_KEYS,UNIT_KEYS);u['unit_id']=ref
            relations=u['relations'];scope=u['attributes'].get('modifier_scope',[]) if isinstance(u['attributes'],dict) else []
            u['relations']=[]
            if isinstance(u['attributes'],dict):u['attributes'].pop('modifier_scope',None)
            out.append(unit(u,source));links[ref]=(relations,scope,path)
        except (ValueError,TypeError,KeyError,IndexError) as e:unmodeled_row(pending,origin,path,str(e),row,ref)
    current={u['unit_id']:u for u in out}
    for u in out:
        relations,scopes,path=links[u['unit_id']];explicit_empty_scopes=isinstance(scopes,list) and not scopes
        if not isinstance(relations,list) or len(relations)>40:
            unmodeled_row(pending,origin,path+'/relations','array_contract',relations,u['unit_id']);relations=[]
        for i,rawlink in enumerate(relations):
            try:
                r=copy.deepcopy(rawlink)
                if not isinstance(r,dict) or r.get('target_unit_id') not in remap or remap[r['target_unit_id']] not in current:raise ValueError('relation_target_missing_or_quarantined')
                r['target_unit_id']=remap[r['target_unit_id']];probe=copy.deepcopy(u);probe['relations']=[r];r=unit(probe,source)['relations'][0];u['relations'].append(r)
            except (ValueError,TypeError,KeyError,IndexError) as e:unmodeled_row(pending,origin,path+'/relations/'+str(i),str(e),rawlink,u['unit_id'])
        if not isinstance(scopes,list) or len(scopes)>30:
            unmodeled_row(pending,origin,path+'/attributes/modifier_scope','array_contract',scopes,u['unit_id']);scopes=[]
        accepted=[]
        for i,rawscope in enumerate(scopes):
            try:
                scope=copy.deepcopy(rawscope)
                if not isinstance(scope,dict):raise ValueError('object_contract_keys')
                if scope.get('target') is not None:
                    target=scope['target']
                    if not isinstance(target,dict) or target.get('unit_id') not in remap or remap[target['unit_id']] not in current:raise ValueError('scope_target_missing_or_quarantined')
                    target['unit_id']=remap[target['unit_id']];pointer(current[target['unit_id']],target['field_path'])
                probe=copy.deepcopy(u);probe['attributes']['modifier_scope']=[scope];scope=unit(probe,source)['attributes']['modifier_scope'][0];accepted.append(scope)
            except (ValueError,TypeError,KeyError,IndexError) as e:unmodeled_row(pending,origin,path+'/attributes/modifier_scope/'+str(i),str(e),rawscope,u['unit_id'])
        if accepted or explicit_empty_scopes and 'modifier_scope' in rows[int(u['unit_id'][1:])-1].get('attributes',{}):u['attributes']['modifier_scope']=accepted
    validate_links(out);return out


def source_audit_view(state_json):return {'audit_input_json':dump({'source':state_load(state_json)['source']})}


def stage_source_audit(state_json,audit_json):
    s=state_load(state_json)
    try:
        pending=[];d=root_fields(parse(s,audit_json,'blind_inventory'),{'units','source_notes','inventory_complete'},{'units','source_notes','inventory_complete'},pending,'blind_inventory')
        rows=owned_units(d['units'],s['source'],'a',pending);rejected=retain_unmodeled(s,pending)
        if not rows or type(d['inventory_complete'])is not bool:raise ValueError('blind_inventory_nonempty_flag')
        notes=[]
        for n in bounded_list(d['source_notes'],50):
            only(n,{'spans','reason','note'},{'spans','reason','note'});notes.append({'spans':spans(n['spans'],s['source']),'reason':n['reason'],'note':n['note']})
        s['inventory']=rows;s['model_inventory_complete']=d['inventory_complete'];s['inventory_complete']=d['inventory_complete'] and not pending;s['source_notes']=notes
        for u in rows:s['revisions'][u['unit_id']]=1
        log(s,'blind_inventory',rejected=rejected,info={'count':len(rows),'only_complete_source_seen':True,'unmodeled_count':len(pending),'contract_recovery_is_not_semantic_repair':True})
    except (ValueError,TypeError,KeyError,IndexError) as e:log(s,'blind_inventory',rejected=[{'code':str(e)}]);s['stop']=True;s['stop_reason']='blind_inventory_failed_incomplete'
    return output(s)


def refs(s):
    out={u['unit_id']:{'type':'source' if u['unit_id'].startswith('a') else 'fact','revision':s['revisions'][u['unit_id']],'value':u} for u in s['inventory']+s['units']}
    out.update({m['mapping_ref']:{'type':'mapping','revision':m['revision'],'value':m} for m in s['mappings']})
    out.update({b['binding_ref']:{'type':'binding','revision':b['revision'],'value':b,'mapping_ref':m['mapping_ref']} for m in s['mappings'] for b in m['bindings'] if 'binding_ref' in b})
    return out


def evidence(s,raw):
    rows={r['line_no']:r['text'] for r in s['source']['records']};out=[]
    for x in bounded_list(raw,30):
        only(x,{'line_no','quote'},{'line_no','quote'})
        if type(x['line_no'])is not int or x['line_no'] not in rows or not isinstance(x['quote'],str) or not x['quote'] or x['quote'] not in rows[x['line_no']]:raise ValueError('evidence_not_actual_source')
        out.append(copy.deepcopy(x))
    if not out:raise ValueError('actual_evidence_required')
    return out


def fact_public(s,u):return dict({k:copy.deepcopy(v) for k,v in u.items() if k!='unit_id'},target_ref=u['unit_id'],revision=s['revisions'][u['unit_id']])


def target_current(s,t):
    u=units_by_id(s).get(t['unit_id'])
    return bool(u is not None and s['revisions'][t['unit_id']]==t['revision'] and sha(pointer(u,t['field_path']))==t['value_sha256'])


# Structural source facets are not semantic gold; unknown clinical fields stay explicit.
QUALIFIER_KEYS={'severity','grade','stage','degree','acuity','course','clinical_grade','clinical_stage'}
CONTEXT_KEYS={'polarity','negation','certainty','status','diagnostic_status','temporality','time','record_time','event_time','source','source_type','source_document','author_wording','role','semantic_role','modifier_scope','normalization','scope','performed','subject','participant','actor','experiencer'}

def leaves(value,path):
    if isinstance(value,dict):
        if not value:yield path,value
        for k,v in value.items():yield from leaves(v,path+'/'+str(k).replace('~','~0').replace('/','~1'))
    elif isinstance(value,list):
        if not value:yield path,value
        for i,v in enumerate(value):yield from leaves(v,path+'/'+str(i))
    else:yield path,value

def source_facets(s):
    out=[]
    for u in s['units']:
        rows=[('/label',u['label'],'coding_required' if u['kind'] in ('event','document') else 'unclassified','core')]
        for k in ('subject','value','unit','site'):
            val=u[k]
            if val is None:continue
            intent='coding_required' if k in ('site','unit') and isinstance(val,str) and val.strip() else 'context_only'
            rows.append(('/'+k,val,intent,k))
        for path,val in leaves(u['attributes'],'/attributes'):
            parts=path.split('/');root=parts[2] if len(parts)>2 else '';tail=parts[-1]
            if val is None or val==[] or val=={}:intent='context_only'
            elif root=='term_components' and isinstance(val,str) and val.strip():intent='coding_required'
            elif root in QUALIFIER_KEYS and isinstance(val,str) and val.strip():intent='coding_required'
            elif root in CONTEXT_KEYS or root.endswith(('_source','_time','_status')) or tail in ('quote','line_no','field_path','unit_id','target_unit_id'):intent='context_only'
            else:intent='unclassified'
            rows.append((path,val,intent,'component' if root=='term_components' else 'qualifier'))
        for i,r in enumerate(u['relations']):rows.append(('/relations/'+str(i),copy.deepcopy(r),'context_only','relation'))
        for path,val,intent,purpose in rows:
            out.append({'facet_ref':u['unit_id']+':'+path,'unit_id':u['unit_id'],'revision':s['revisions'][u['unit_id']],'field_path':path,'value':copy.deepcopy(val),'value_sha256':sha(val),'source_preserved':True,'source_lines':[x['line_no'] for x in u['spans']],'intent':intent,'purpose':purpose})
    return out

def ensure_tasks(s):
    existing={(t['unit_id'],t['field_path'],t['revision']) for t in s['tasks']}
    for facet in source_facets(s):
        if facet['intent']!='coding_required':continue
        uid=facet['unit_id'];path=facet['field_path'];val=facet['value'];ident=(uid,path,facet['revision'])
        if ident in existing:continue
        prior=next((t for t in reversed(s['tasks']) if t['unit_id']==uid and t['field_path']==path and t['value_sha256']==sha(val)),None)
        if prior:
            prior['revision']=ident[2];prior['status']='not_selected';existing.add(ident);continue
        s['tasks'].append({'target_ref':'t'+str(len(s['tasks'])+1),'unit_id':uid,'field_path':path,'revision':ident[2],'value':copy.deepcopy(val),'value_sha256':sha(val),'purpose':facet['purpose'],'status':'not_attempted','query_status':'not_attempted','candidate_refs':[],'expansion_attempted':False});existing.add(ident)
    s['source_facets']=source_facets(s)

def binding_owner(s,ref):
    for m in s['mappings']:
        for b in m['bindings']:
            if b.get('binding_ref')==ref:return m,b
    return None,None

def active_binding_targets(m):
    return {r for b in m['bindings'] if b.get('status')!='invalidated' for r in b['covers']}

def binding_invalidate(s,ref,reason=None):
    m,b=binding_owner(s,ref)
    if b is None:return
    b['status']='invalidated';s['mapping_checks'].pop(ref,None)
    if reason:
        issue=next((i for i in s['issues'] if i['issue_ref']==reason),{})
        semantic_conflict=any(i['target_ref']==ref and i['expected_revision']==b['revision'] and i['kind'] in MAPPING_KINDS and not i.get('evidence_gate_only') for i in s['issues'])
        if (issue.get('kind') in RATIONALE_KINDS or issue.get('evidence_gate_only')) and not semantic_conflict:
            key=sha({'binding_fingerprint':b.get('fingerprint',sha(b)),'reason':m['reason'].strip()})
            s.setdefault('rejected_rationales',{})[key]={'issue_ref':reason,'binding_ref':ref,'revision':b['revision']}
        else:s['rejected_fingerprints'][b.get('fingerprint',sha(b))]=reason
    for r in b['covers']:
        t=task_by_ref(s,r);t['status']='not_selected';t.pop('unresolved_reason',None)
    refresh_mapping(s,m)

def refresh_mapping(s,m):
    statuses=[b.get('status','pending_review') for b in m['bindings']]
    m['status']='invalidated' if all(x=='invalidated' for x in statuses) else 'pending_review' if 'pending_review' in statuses else 'proposed_partial' if any(x not in ACCEPTED_BINDING_STATUSES for x in statuses) or m['residuals'] else 'proposed_label_association' if 'proposed_label_association' in statuses else 'proposed_equivalent'

def candidate_card(s,ref):
    c=s['catalog'][str(s['candidate_refs'][ref])]
    keys=set(CONCEPT_FIELDS)|{'synonyms','concept_synonyms','matched_terms','eligibility','verified_at'}
    card={k:copy.deepcopy(v) for k,v in c.items() if k in keys};card['candidate_ref']=ref
    card['meaning_evidence_refs']=meaning_refs(s,ref)
    card['vocabulary_version_ref']='v'+sha([c.get('vocabulary_id'),c.get('vocabulary_version')])[:16];return card

def meaning_evidence_view(bundle,versions):
    def version_ref(identity):
        pair=[identity.get('vocabulary_id'),identity.get('vocabulary_version')];ref='v'+sha(pair)[:16]
        versions[ref]={'vocabulary_id':pair[0],'vocabulary_version':pair[1]};return ref
    source=bundle.get('source') or {};targets={};relations=[];types={}
    for row in bundle['relations']:
        identity=row['target_identity'];target=str(row['target_id'])
        targets[target]={k:copy.deepcopy(identity[k]) for k in CONCEPT_FIELDS if k!='concept_id'}
        targets[target]['vocabulary_version_ref']=version_ref(identity)
        relations.append({k:copy.deepcopy(row[k]) for k in ('source_id','relationship_id','target_id','direction','valid_start_date','valid_end_date','invalid_reason','is_hierarchical','defines_ancestry','reverse_relationship_id','relationship_concept_id')})
        types[row['relationship_id']]={key:copy.deepcopy(row.get(key)) for key in ('relationship_name','is_hierarchical','defines_ancestry','reverse_relationship_id','relationship_concept_id')}
    return dict({k:copy.deepcopy(bundle[k]) for k in ('meaning_ref','concept_id','verification_date','availability','caps','counts','truncated')},source_vocabulary_version_ref=version_ref(source),synonym_source_table='concept_synonym',synonyms=[{k:copy.deepcopy(row[k]) for k in ('term','language_concept_id','description_type')} for row in bundle['synonyms']],relations=relations,relation_targets=targets,relationship_types=types)

def meaning_projection(s,wanted,versions,details=True):
    inventory={};bundles={};targets={};types={}
    for ref in wanted:
        full=s['meaning_evidence'][ref];source=full.get('source') or {};vr='v'+sha([source.get('vocabulary_id'),source.get('vocabulary_version')])[:16]
        versions[vr]={'vocabulary_id':source.get('vocabulary_id'),'vocabulary_version':source.get('vocabulary_version')}
        provenance=copy.deepcopy(s.get('meaning_provenance',{}).get(ref,[]))
        inventory[ref]=dict({k:copy.deepcopy(full[k]) for k in ('meaning_ref','concept_id','verification_date','availability','caps','counts','truncated')},source_vocabulary_version_ref=vr,provenance=provenance,provenance_status='actual_receipt_recorded' if provenance else 'not_recorded_in_legacy_state')
        if not details:continue
        card=meaning_evidence_view(full,versions)
        endpoints=card.pop('relation_targets');relation_types=card.pop('relationship_types')
        for relation in card['relations']:
            identity=dict(copy.deepcopy(endpoints[str(relation['target_id'])]),concept_id=relation['target_id']);target_ref='mt'+sha(identity)[:20]
            typ=dict(copy.deepcopy(relation_types[relation['relationship_id']]),relationship_id=relation['relationship_id']);type_ref='my'+sha(typ)[:20]
            targets[target_ref]=identity;types[type_ref]=typ;relation['target_identity_ref']=target_ref;relation['relationship_type_ref']=type_ref
        bundles[ref]=card
    return {'meaning_inventory':inventory,'meaning_evidence':bundles,'meaning_targets':targets,'meaning_types':types}

def projected_evidence(s,cids,meaning_details=True):
    concepts={s['candidate_refs'][r] for r in cids}
    relations={r:{k:copy.deepcopy(v) for k,v in x.items() if k in ('source_id','target_id','relationship_id','relationship_type','receipt_ref','verification_date','valid_start_date','valid_end_date','invalid_reason','evidence_id')} for r,x in s['relationships'].items() if x.get('source_id') in concepts and x.get('target_id') in concepts}
    receipts={x['receipt_ref']:{vid:'v'+sha([vid,ver])[:16] for vid,ver in x.get('vocabulary_versions',{}).items()} for x in s['relationships'].values() if x.get('receipt_ref') and x.get('source_id') in concepts and x.get('target_id') in concepts}
    versions={'v'+sha([s['catalog'][str(cid)].get('vocabulary_id'),s['catalog'][str(cid)].get('vocabulary_version')])[:16]:{'vocabulary_id':s['catalog'][str(cid)].get('vocabulary_id'),'vocabulary_version':s['catalog'][str(cid)].get('vocabulary_version')} for cid in concepts}
    for x in s['relationships'].values():
        if x.get('source_id') in concepts and x.get('target_id') in concepts:
            for vid,ver in x.get('vocabulary_versions',{}).items():versions['v'+sha([vid,ver])[:16]]={'vocabulary_id':vid,'vocabulary_version':ver}
    meaning=meaning_projection(s,{ref for cid in cids for ref in meaning_refs(s,cid)},versions,meaning_details)
    return dict({'candidates':{r:candidate_card(s,r) for r in cids},'relationships':relations,'vocabulary_versions':versions,'relationship_receipt_versions':receipts},**meaning)


def task_by_ref(s,ref):
    t=next((t for t in s['tasks'] if t['target_ref']==ref),None)
    if t is None or not target_current(s,t):raise ValueError('unknown_or_stale_task_target')
    return t


def actual_candidates(s,t):return [x for x in t['candidate_refs'] if x in s['candidate_refs']]


def retrieval_assessment(s,t):
    refs_=actual_candidates(s,t);counts={'eligible_standard':0,'pending_metadata':0,'not_eligible':0};reasons=[]
    for ref in refs_:
        c=s['catalog'].get(str(s['candidate_refs'][ref])) or {}
        status=c.get('eligibility','pending_metadata');counts[status if status in counts else 'pending_metadata']+=1
    if t['query_status']=='not_attempted':reasons.append('original_query_not_executed')
    else:
        if not refs_:reasons.append('no_candidate_in_actual_receipts')
        if not counts['eligible_standard']:reasons.append('no_eligible_standard_candidate')
        if counts['pending_metadata']:reasons.append('candidate_metadata_pending')
        if t['query_status']=='incomplete':reasons.append('query_incomplete')
        if any(q['target_ref']==t['target_ref'] and (q['actual_stats'].get('truncated') or str(q['actual_stats'].get('phrase_stage','')).startswith('skipped')) for q in s['queries']):reasons.append('query_truncated_or_stage_skipped')
    return {'candidate_count':len(refs_),'eligible_standard_count':counts['eligible_standard'],'pending_metadata_count':counts['pending_metadata'],'not_eligible_count':counts['not_eligible'],'reasons':reasons,'last_expansion_feedback':copy.deepcopy(t.get('expansion_feedback')),'needs_expansion':t['query_status']!='not_attempted' and (not counts['eligible_standard'] or t['query_status']=='incomplete' or 'query_truncated_or_stage_skipped' in reasons),'exhaustive':False}


def review_projection(s,bindings):
    cids={b['candidate_ref'] for b in bindings};relrefs={b['relationship_ref'] for b in bindings if b.get('relationship_ref') is not None}
    relevant={ref:s['relationships'][ref] for ref in relrefs if ref in s['relationships']}
    endpoints={cid for row in relevant.values() for cid in (row['source_id'],row['target_id'])}
    cids.update(ref for ref,cid in s['candidate_refs'].items() if cid in endpoints)
    out=projected_evidence(s,cids);out['relationships']={ref:row for ref,row in out['relationships'].items() if ref in relrefs}
    receipts={row['receipt_ref'] for row in out['relationships'].values() if row.get('receipt_ref')}
    out['relationship_receipt_versions']={ref:versions for ref,versions in out['relationship_receipt_versions'].items() if ref in receipts}
    bound_meaning={ref for b in bindings for ref in meaning_refs(s,b['candidate_ref'])};out.update(meaning_projection(s,bound_meaning,out['vocabulary_versions']))
    needed_versions={card['vocabulary_version_ref'] for card in out['candidates'].values()}|{ref for versions in out['relationship_receipt_versions'].values() for ref in versions.values()}|{row['source_vocabulary_version_ref'] for row in out['meaning_inventory'].values()}|{t['vocabulary_version_ref'] for t in out['meaning_targets'].values()}
    out['vocabulary_versions']={ref:row for ref,row in out['vocabulary_versions'].items() if ref in needed_versions}
    return out


def rationale_candidate_handoff(s,view,mappings):
    """Only actual reason-mentioned identities/relations within each current target pool."""
    unavailable={};scopes={};relation_scopes={}
    keys=set(CONCEPT_FIELDS)|{'candidate_ref','eligibility','verified_at','vocabulary_version_ref'}
    for mapping in mappings:
        mentioned=set(re.findall(r'(?<![A-Za-z0-9_])c[0-9]+(?![A-Za-z0-9_])',mapping['reason']))
        pool={ref for task in s['tasks'] if task['target_ref'] in mapping['target_refs'] and target_current(s,task) for ref in actual_candidates(s,task)}
        current_tasks={task['target_ref']:task for task in s['tasks']}
        mapping_current=all(ref in current_tasks and target_current(s,current_tasks[ref]) for ref in mapping['target_refs'])
        bound={b['candidate_ref'] for b in mapping['bindings'] if b.get('status')!='invalidated'} if mapping_current else set()
        if not mapping_current:pool=set()
        explicit_rel={ref for ref in s['relationships'] if re.search(r'(?<![A-Za-z0-9_])'+re.escape(ref)+r'(?![A-Za-z0-9_])',mapping['reason'])}|{b.get('relationship_ref') for b in mapping['bindings'] if b.get('relationship_ref')}
        pool_ids={s['candidate_refs'][ref] for ref in pool|bound}
        rel_endpoints={cid for ref in explicit_rel for row in [s['relationships'][ref]] if row['source_id'] in pool_ids and row['target_id'] in pool_ids for cid in (row['source_id'],row['target_id'])}
        allowed=(mentioned&pool&set(s['candidate_refs']))|bound|{ref for ref in pool if s['candidate_refs'][ref] in rel_endpoints}
        unavailable[mapping['mapping_ref']]=sorted(mentioned-allowed)
        scopes[mapping['mapping_ref']]=sorted(allowed)
        for ref in sorted(allowed-set(view['candidates'])):
            card=candidate_card(s,ref);view['candidates'][ref]={key:copy.deepcopy(value) for key,value in card.items() if key in keys}
            view['candidates'][ref]['rationale_only']=True
            concept=s['catalog'][str(s['candidate_refs'][ref])]
            view['vocabulary_versions'][card['vocabulary_version_ref']]={'vocabulary_id':concept.get('vocabulary_id'),'vocabulary_version':concept.get('vocabulary_version')}
        endpoints={s['candidate_refs'][ref] for ref in allowed}
        for ref,row in s['relationships'].items():
            if row['source_id'] not in endpoints or row['target_id'] not in endpoints or row['source_id']==row['target_id'] and ref not in explicit_rel:continue
            view['relationships'][ref]={key:copy.deepcopy(value) for key,value in row.items() if key in ('source_id','target_id','relationship_id','valid_start_date','valid_end_date','invalid_reason','evidence_id','receipt_ref','verification_date')}
            relation_scopes.setdefault(ref,[]).append(mapping['mapping_ref'])
            view['relationship_receipt_versions'][row['receipt_ref']]={}
            for cid in (row['source_id'],row['target_id']):
                concept=s['catalog'][str(cid)];vid=concept.get('vocabulary_id');ver=row.get('vocabulary_versions',{}).get(vid,concept.get('vocabulary_version'));vr='v'+sha([vid,ver])[:16]
                view['vocabulary_versions'][vr]={'vocabulary_id':vid,'vocabulary_version':ver};view['relationship_receipt_versions'][row['receipt_ref']][vid]=vr
    view['rationale_scopes']=scopes;view['rationale_relation_scopes']=relation_scopes
    view['rationale_unavailable_refs']={ref:rows for ref,rows in unavailable.items() if rows}


def reason_claims(s,mappings):
    """Mechanical, lossless spans; these are review obligations, not inferred claims."""
    rows=[]
    for m in mappings:
        text=m['reason'];digest=hashlib.sha256(text.encode()).hexdigest();offset=0;parts=[]
        for match in re.finditer(r'.*?(?:[,，;；.。—–]+|$)',text,re.S):
            part=match.group()
            if not part:continue
            if not part.strip() and parts:parts[-1]+=part
            else:parts.append(part)
        for part in parts:
            end=offset+len(part)
            rows.append({'claim_ref':'rc'+sha([m['mapping_ref'],digest,offset,end,part])[:20],'mapping_ref':m['mapping_ref'],'reason_hash':digest,'char_start':offset,'char_end':end,'text':part})
            offset=end
        if offset!=len(text):raise ValueError('reason_claim_lossless_span_invariant')
    return rows


def review_evidence_registry(s,view):
    """Scope-local refs with directed relationship statements and original provenance."""
    rows={};scopes=view.get('rationale_scopes',{})
    snapshot=content_sha({'source_sha256':s['source']['source_sha256'],'bindings':[binding_semantics(b) for b in view.get('bindings',[])],
                  'vocabulary_versions':view.get('vocabulary_versions',{}),'meaning_inventory':view.get('meaning_inventory',{}),'relationships':view.get('relationships',{})})
    def register(kind,text,candidate_ref=None,meaning_ref=None,path=None,source_row=None,mapping_refs=None,binding_refs=None):
        if not isinstance(text,str) or not text:return
        candidates=candidate_ref if isinstance(candidate_ref,list) else [candidate_ref]
        if mapping_refs is None:mapping_refs=[mref for mref,refs_ in scopes.items() if any(ref in refs_ for ref in candidates)]
        if binding_refs is None:binding_refs=[b['binding_ref'] for b in view.get('bindings',[]) if b['candidate_ref'] in candidates]
        value={'kind':kind,'text':text,'candidate_ref':candidate_ref,'meaning_ref':meaning_ref,'path':path,'source_row_sha256':content_sha(source_row),'mapping_refs':sorted(mapping_refs),'binding_refs':sorted(binding_refs),'evidence_snapshot_sha256':snapshot}
        digest=content_sha(value);rows[digest]=dict(value,registry_row_sha256=digest)
    for ref,card in sorted(view.get('candidates',{}).items()):
        card.pop('evidence_refs',None)
        for key in CONCEPT_FIELDS:
            if key not in card:continue
            value=card[key];kind='candidate_label' if key=='concept_name' else 'candidate_metadata'
            text=value if kind=='candidate_label' else dump({key:value})
            register(kind,text,ref,path=['candidates',ref,key],source_row={key:value})
    for mr,bundle in sorted(view.get('meaning_evidence',{}).items()):
        current=sorted(ref for ref in view.get('candidates',{}) if mr in meaning_refs(s,ref))
        for ix,row in enumerate(bundle.get('synonyms',[])):
            row.pop('evidence_ref',None)
            register('candidate_synonym',row['term'],current,mr,['meaning_evidence',mr,'synonyms',ix],row)
        for ix,row in enumerate(bundle.get('relations',[])):
            row.pop('evidence_ref',None)
            source=s['catalog'].get(str(row['source_id']),{});target=view['meaning_targets'][row['target_identity_ref']]
            source_version=bundle.get('source_vocabulary_version_ref');target_version=target.get('vocabulary_version_ref')
            statement={key:copy.deepcopy(row[key]) for key in ('source_id','target_id','relationship_id','direction','valid_start_date','valid_end_date','invalid_reason','is_hierarchical','defines_ancestry','reverse_relationship_id','relationship_concept_id')}
            statement.update(source_identity={key:copy.deepcopy(source[key]) for key in ('concept_id','concept_name','vocabulary_id') if key in source},target_identity={key:copy.deepcopy(target[key]) for key in ('concept_id','concept_name','vocabulary_id') if key in target},verification_date=bundle.get('verification_date'),provenance=copy.deepcopy(view['meaning_inventory'].get(mr,{}).get('provenance',[])))
            statement['source_identity']['vocabulary_version_ref']=source_version;statement['target_identity']['vocabulary_version_ref']=target_version
            register('directed_vocabulary_relationship',dump(statement),current,mr,['meaning_evidence',mr,'relations',ix],s['meaning_evidence'][mr]['relations'][ix])
    for ref,row in sorted(view.get('relationships',{}).items()):
        row.pop('evidence_ref',None)
        statement=dict(copy.deepcopy(row),relationship_ref=ref,direction='source_to_target')
        for side in ('source','target'):
            identity=s['catalog'].get(str(row[side+'_id']),{})
            statement[side+'_identity']={key:copy.deepcopy(identity[key]) for key in ('concept_id','concept_name','vocabulary_id') if key in identity}
            statement[side+'_identity']['vocabulary_version_ref']=view['relationship_receipt_versions'].get(row.get('receipt_ref'),{}).get(identity.get('vocabulary_id'))
        register('vocabulary_mapping_relationship',dump(statement),None,None,['relationships',ref],s['relationships'][ref],view.get('rationale_relation_scopes',{}).get(ref,[]),
                 [b['binding_ref'] for b in view.get('bindings',[]) if s['candidate_refs'][b['candidate_ref']] in (row['source_id'],row['target_id'])])
    for binding in view.get('bindings',[]):
        mref=binding['mapping_ref'];tasks=[task for task in view.get('retrieval_targets',[]) if task['target_ref'] in binding['covers']]
        unitids={task['unit_id'] for task in tasks}
        context={'binding':{key:copy.deepcopy(binding[key]) for key in ('binding_ref','candidate_ref','role','relation','covers')},'targets':[{key:copy.deepcopy(task[key]) for key in ('target_ref','unit_id','field_path','revision','value','purpose')} for task in tasks],'facts':[{key:copy.deepcopy(unit[key]) for key in ('unit_id','subject','value','unit','site','attributes','relations')} for unit in s['units'] if unit['unit_id'] in unitids]}
        register('binding_context',dump(context),binding['candidate_ref'],path=['bindings',binding['binding_ref'],'context'],source_row=context,mapping_refs=[mref],binding_refs=[binding['binding_ref']])
    for record in s['source']['records']:
        mrefs=[mref for mref in scopes if any(sp['line_no']==record['line_no'] for mapping in s['mappings'] if mapping['mapping_ref']==mref for dep in mapping['dependencies'] for sp in units_by_id(s)[dep['target_ref']]['spans'])]
        if mrefs:register('source_record',record['text'],path=['source','records',record['line_no']],source_row=record,mapping_refs=mrefs,
                          binding_refs=[b['binding_ref'] for b in view.get('bindings',[]) if any(record['line_no']==sp['line_no'] for target in b['covers'] for sp in units_by_id(s)[task_by_ref(s,target)['unit_id']]['spans'])])
    registry={'e'+digest[:24]:rows[digest] for digest in sorted(rows)}
    if len(registry)!=len(rows):raise ValueError('evidence_content_ref_collision')
    view['evidence_rows']={ref:{key:copy.deepcopy(row[key]) for key in ('kind','candidate_ref','text','mapping_refs','binding_refs')} for ref,row in registry.items()}
    view['evidence_snapshot_sha256']=snapshot
    view['evidence_scope_sha256']=sha({ref:row for ref,row in view['evidence_rows'].items()})
    return registry


def rationale_scope_digest(view,mapping_ref):
    rows=[{key:copy.deepcopy(row[key]) for key in ('kind','candidate_ref','text')} for row in view['evidence_rows'].values() if mapping_ref in row['mapping_refs']]
    return sha(sorted(rows,key=dump))


def verified_structured_projection(proposed,actual):
    """Typed field projection only; missing fields cannot invent a different value."""
    if type(proposed) is not type(actual):return False
    if isinstance(proposed,dict):return (bool(proposed) or not actual) and all(key in actual and verified_structured_projection(value,actual[key]) for key,value in proposed.items())
    if isinstance(proposed,list):return len(proposed)==len(actual) and all(verified_structured_projection(left,right) for left,right in zip(proposed,actual))
    return proposed==actual


def structured_quote(quote,text):
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError('duplicate_structured_quote_key')
            out[key]=value
        return out
    try:
        if len(quote)>20000:return None
        proposed=json.loads(quote,object_pairs_hook=unique);actual=json.loads(text)
        if not isinstance(proposed,dict) or not proposed or not verified_structured_projection(proposed,actual):return None
        return {'fields':proposed,'actual_row_sha256':sha(actual),'verified_field_projection_sha256':sha(proposed)}
    except (ValueError,TypeError,RecursionError):return None


def review_basis(rows,registry,candidate_ref=None,purpose=None,mapping_ref=None,binding_ref=None):
    checked=[]
    for row in bounded_list(rows,24):
        required={'evidence_ref'}|({'purpose'} if purpose is not None else set())
        only(row,required,required)
        if purpose is not None and row['purpose'] not in ('association','forward','reverse'):raise ValueError('review_basis_purpose')
        evidence_row=registry.get(row['evidence_ref'])
        if evidence_row is None:raise ValueError('review_basis_not_actual_current_view_ref')
        if mapping_ref is not None and mapping_ref not in evidence_row['mapping_refs']:raise ValueError('rationale_basis_outside_mapping_scope')
        if binding_ref is not None and binding_ref not in evidence_row['binding_refs']:raise ValueError('review_basis_outside_binding_scope')
        scoped=evidence_row['candidate_ref']
        if candidate_ref is not None and scoped is not None and candidate_ref not in (scoped if isinstance(scoped,list) else [scoped]):raise ValueError('review_basis_outside_binding_candidate_scope')
        value=dict(copy.deepcopy(row),quote=evidence_row['text'],quote_origin='code_projected_current_row_not_model_quotation',
                   evidence_row_sha256=evidence_row['registry_row_sha256'],evidence_snapshot_sha256=evidence_row['evidence_snapshot_sha256'],evidence_path=copy.deepcopy(evidence_row['path']))
        checked.append(value)
    return checked

def binding_source_evidence(s,binding):
    tasks=[task_by_ref(s,ref) for ref in binding['covers']]
    lines=sorted({span['line_no'] for task in tasks for span in units_by_id(s)[task['unit_id']]['spans']})
    records={row['line_no']:row['text'] for row in s['source']['records']}
    return evidence(s,[{'line_no':line,'quote':records[line]} for line in lines])

def source_evidence_from_refs(s,registry,refs_,mapping_refs):
    rows=[]
    for ref in bounded_list(refs_,30):
        row=registry.get(ref) if isinstance(ref,str) else None
        if row is None or row['kind']!='source_record' or not set(row['mapping_refs'])&set(mapping_refs):raise ValueError('issue_source_ref_not_current_scoped_record')
        rows.append({'line_no':row['path'][-1],'quote':row['text']})
    return evidence(s,rows)


def assertion_obligations(s,bindings):
    rows=[]
    for binding in bindings:
        for predicate in ('source_association','source_equivalence') if binding['relation']=='equivalent' else ('source_association',):
            row={'predicate':predicate,'binding_ref':binding['binding_ref'],'revision':binding['revision'],'candidate_ref':binding['candidate_ref'],'target_refs':copy.deepcopy(binding['covers'])}
            row['assertion_ref']='a'+sha([row,s['source']['source_sha256']])[:20];rows.append(row)
    return rows


def validate_structured_mapping_review(s,call,d):
    only(d,{'checks','assertion_checks','issues'},{'checks','assertion_checks','issues'})
    requested={x['target_ref']:x['revision'] for x in call['targets']};view=view_for_call(s);registry=review_evidence_registry(s,view)
    obligations={row['assertion_ref']:row for row in view['assertion_obligations']};assertions={};checks={};rejected=[];seen=set();bad_assertions=set();check_seen=set();bad_checks=set();raw_negatives=[];raw_assertion_negatives=[];identity_verified=set()
    for row in bounded_list(d['assertion_checks'],400):
        ref=row.get('assertion_ref') if isinstance(row,dict) else None
        try:
            required={'assertion_ref','verdict','basis','note'};only(row,required,required)
            if not isinstance(ref,str) or ref not in obligations:raise ValueError('actual_assertion_exact_once_required')
            obligation=obligations[ref];mapping,binding=binding_owner(s,obligation['binding_ref'])
            if row['verdict'] not in ('supported','unsupported','not_checkable'):raise ValueError('assertion_verdict')
            if row['verdict'] in ('unsupported','not_checkable'):raw_assertion_negatives.append({'assertion_check':copy.deepcopy(row),'obligation':copy.deepcopy(obligation),'binding_refs':[binding['binding_ref']]})
            if ref in seen:raise ValueError('actual_assertion_exact_once_required')
            seen.add(ref)
            if not isinstance(row['note'],str) or not 1<=len(row['note'].strip())<=160:raise ValueError('review_short_note_required')
            basis=review_basis(row['basis'],registry,binding['candidate_ref'],mapping_ref=mapping['mapping_ref'],binding_ref=binding['binding_ref'])
            if row['verdict']=='supported' and not basis:raise ValueError('supported_assertion_requires_actual_basis')
            if row['verdict']=='supported' and all(registry[item['evidence_ref']]['kind']=='candidate_metadata' for item in basis):raise ValueError('source_predicate_not_supported_by_metadata_only')
            assertions[ref]=dict(copy.deepcopy(row),basis=basis)
        except (ValueError,TypeError,KeyError,IndexError) as error:
            if isinstance(ref,str) and ref in obligations:bad_assertions.add(ref);assertions.pop(ref,None)
            rejected.append({'code':str(error),'assertion_ref':ref,'proposal':copy.deepcopy(row),'contract_only':True})
    for ref in bad_assertions:assertions.pop(ref,None)
    for check in bounded_list(d['checks'],200):
        ref=check.get('target_ref') if isinstance(check,dict) else None
        try:
            required={'target_ref','revision','candidate_ref','assessment','basis','note'};only(check,required,required)
            m,b=binding_owner(s,ref)
            if not isinstance(ref,str) or ref not in requested or ref in check_seen or b is None or type(check['revision']) is not int or check['revision']!=requested[ref] or b['revision']!=requested[ref]:raise ValueError('check_actual_requested_binding_revision_required')
            check_seen.add(ref)
            if check['candidate_ref']!=b['candidate_ref']:raise ValueError('check_candidate_must_match_binding')
            c=s['catalog'][str(s['candidate_refs'][b['candidate_ref']])];ev=binding_source_evidence(s,b)
            identity_verified.add(ref)
            assessment=check['assessment'];only(assessment,{'association','forward','reverse'},{'association','forward','reverse'})
            if assessment['association'] not in ('supported','unsupported','not_checkable') or any(assessment[k] not in ('supported','unsupported','not_checkable','not_claimed') for k in ('forward','reverse')):raise ValueError('binding_assessment_verdict')
            if assessment['association']!='supported' or b['relation']=='equivalent' and any(assessment[key]!='supported' for key in ('forward','reverse')):raw_negatives.append({'binding_check':copy.deepcopy(check),'binding_refs':[ref]})
            if not isinstance(check['note'],str) or not 1<=len(check['note'].strip())<=160:raise ValueError('review_short_note_required')
            bases=review_basis(check['basis'],registry,b['candidate_ref'],True,m['mapping_ref'],b['binding_ref'])
            for purpose in ('association','forward','reverse'):
                scoped=[x for x in bases if x['purpose']==purpose]
                if assessment[purpose]=='supported' and not scoped:raise ValueError('supported_assessment_requires_actual_basis')
            checks[ref]=dict(copy.deepcopy(check),basis=bases,source_evidence=ev,candidate_quote=c['concept_name'],
                             identity_evidence_origin='code_current_binding_source_and_candidate_not_model_quotation')
        except (ValueError,TypeError,KeyError,IndexError) as error:
            if isinstance(ref,str) and ref in requested:bad_checks.add(ref);checks.pop(ref,None)
            rejected.append({'code':str(error),'proposal':copy.deepcopy(check),'contract_only':True})
    for ref in sorted(set(requested)-set(checks)):rejected.append({'code':'required_binding_not_checked','target_ref':ref,'revision':requested[ref],'contract_only':True})
    for ref in bad_checks:checks.pop(ref,None)
    missing=set(obligations)-set(assertions)
    for ref in sorted(missing):rejected.append({'code':'required_assertion_not_checked','assertion_ref':ref,'target_ref':obligations[ref]['binding_ref'],'contract_only':True})
    incomplete_maps={(binding_owner(s,obligations[ref]['binding_ref'])[0] or {})['mapping_ref'] for ref in missing}
    bounded_list(d['issues'],100)
    raw_negatives.extend(row for row in raw_assertion_negatives if set(row['binding_refs'])<=identity_verified)
    return checks,assertions,obligations,registry,rejected,incomplete_maps,raw_negatives


def binding_semantics(binding):
    return {key:copy.deepcopy(binding.get(key)) for key in ('binding_ref','revision','candidate_ref','role','relation','relation_origin','relationship_ref','covers')}

def issue_evidence_generation(s,ref):
    """Relevant meaning content/versions, never quote windows, receipts or neighbors."""
    parent,binding=binding_owner(s,ref)
    if binding is None:return sha(refs(s)[ref]['value'])
    concept=s['catalog'][str(s['candidate_refs'][binding['candidate_ref']])]
    bundles=[]
    for mr in meaning_refs(s,binding['candidate_ref']):
        bundle=s['meaning_evidence'][mr]
        relation_keys=('source_id','target_id','relationship_id','direction','valid_start_date','valid_end_date','invalid_reason','is_hierarchical','defines_ancestry','reverse_relationship_id','relationship_concept_id','target_identity')
        bundles.append({'source':bundle.get('source'),'synonyms':sorted(bundle.get('synonyms',[]),key=dump),
                        'relations':sorted([{key:copy.deepcopy(row.get(key)) for key in relation_keys} for row in bundle.get('relations',[])],key=dump)})
    tasks=[task_by_ref(s,target) for target in binding['covers']]
    return sha({'concept':{key:copy.deepcopy(concept.get(key)) for key in CONCEPT_FIELDS+('vocabulary_version',)},
                'meaning_content':sorted({sha(bundle):bundle for bundle in bundles}.values(),key=dump),'targets':sorted([{key:copy.deepcopy(t[key]) for key in ('target_ref','unit_id','field_path','revision','value_sha256')} for t in tasks],key=dump),
                'facts':[u for u in s['units'] if u['unit_id'] in {t['unit_id'] for t in tasks}]})

def issue_gate(raw,structured):
    if (structured or {}).get('calibration_only'):
        return 'source_equivalence'
    if raw['kind']=='unsupported_specificity':return 'source_association'
    return (structured or {}).get('gate') or raw['kind']

def issue_for(s,raw,allowed,current_source_checks=None,gate=None,required_action=None):
    only(raw,{'target_ref','kind','evidence_refs','detail'},{'target_ref','kind','evidence_refs','detail'})
    if raw['target_ref'] not in allowed or raw['kind'] not in SOURCE_KINDS|QUERY_KINDS|MAPPING_KINDS|RATIONALE_KINDS:raise ValueError('issue_target_or_kind')
    if not isinstance(raw['detail'],str) or not raw['detail'].strip():raise ValueError('issue_detail')
    ref=raw['target_ref'];facts=units_by_id(s);inventory={u['unit_id'] for u in s['inventory']}
    if raw['kind']=='scope_or_context' and ref not in facts:raise ValueError('scope_issue_requires_actual_fact_target')
    if raw['kind']=='source_omission' and ref not in set(facts)|inventory:raise ValueError('omission_requires_actual_source_target')
    if raw['kind']=='source_omission' and ref in inventory:
        check=s['source_checks'].get(ref,{})
        if ref not in (current_source_checks or set()) or check.get('revision')!=s['revisions'][ref] or check.get('status')!='missing':raise ValueError('inventory_omission_requires_this_review_missing_check')
    ev=evidence(s,raw['evidence_refs']);r=refs(s)[raw['target_ref']]['revision']
    action=required_action or ('patch_fact' if raw['kind'] in SOURCE_KINDS else 'retrieve' if raw['kind'] in QUERY_KINDS else 'revise_rationale' if raw['kind'] in RATIONALE_KINDS else 'replace_mapping')
    _,binding=binding_owner(s,ref)
    identity={'source_sha256':s['source']['source_sha256'],'ref':ref,'revision':r,'kind':raw['kind'],'gate':gate or raw['kind'],'required_action':action,
              'binding_semantics':binding_semantics(binding) if binding else None}
    fp=sha(identity);generation=issue_evidence_generation(s,ref)
    old=next((x for x in s['issues'] if x['fingerprint']==fp),None)
    observation={'detail':raw['detail'],'evidence_refs':ev,'evidence_generation':generation,'wave':s['usage']['loop_waves']}
    if old:
        old.setdefault('observations',[]).append(observation)
        if generation not in old.setdefault('evidence_generations',[]):
            old['evidence_generations'].append(generation);old['attempts']=0;old['status']='open';old['new_actual_evidence_reopened']=True
        elif old['status']=='model_checked_repair_pending_offline':old['status']='open';old['same_gap_reopened_without_attempt_reset']=True
        old.update(detail=raw['detail'],evidence_refs=ev)
        return old
    row=dict(copy.deepcopy(raw),issue_ref='i'+str(len(s['issues'])+1),expected_revision=r,evidence_refs=ev,fingerprint=fp,gap_identity=identity,required_action=action,status='open',attempts=0,evidence_generations=[generation],observations=[observation])
    s['issues'].append(row);return row


def invalidate(s,changed):
    for ref,row in list(s['source_checks'].items()):
        if ref in changed or any(x in changed for x in row.get('fact_refs',[])):del s['source_checks'][ref]
    for m in s['mappings']:
        if any(d['target_ref'] in changed for d in m['dependencies']):
            m['status']='invalidated';s['mapping_checks'].pop(m['mapping_ref'],None)
            for b in m['bindings']:
                b['status']='invalidated';s['mapping_checks'].pop(b.get('binding_ref'),None)
            for ref in m['target_refs']:
                t=next((t for t in s['tasks'] if t['target_ref']==ref),None)
                if t and target_current(s,t):t['status']='not_selected'
    ensure_tasks(s)


def source_patch_target(s,issue,target):
    ref=issue['target_ref'];facts=units_by_id(s);inventory={u['unit_id'] for u in s['inventory']}
    if ref not in refs(s) or issue['expected_revision']!=refs(s)[ref]['revision']:raise ValueError('source_issue_target_revision_stale')
    if issue['kind']=='scope_or_context':
        if ref not in facts or target!=ref:raise ValueError('scope_patch_requires_same_actual_fact')
    elif issue['kind']=='source_omission':
        if ref in inventory:
            check=s['source_checks'].get(ref,{})
            if check.get('revision')!=s['revisions'][ref] or check.get('status')!='missing' or not check.get('model_checked_current_revision'):raise ValueError('inventory_patch_requires_current_missing_check')
            attachments=bounded_list(check['fact_refs'],160)
            if any(r not in facts for r in attachments):raise ValueError('omission_patch_attachment_not_actual_fact')
            if target is None and attachments:raise ValueError('omission_has_existing_fact_requires_local_patch')
            if target is not None and (target not in facts or target not in attachments):raise ValueError('omission_patch_requires_checked_fact_attachment')
        elif ref not in facts or target!=ref:raise ValueError('fact_omission_requires_existing_fact_patch')
    else:raise ValueError('source_patch_issue_kind')


def set_field(u,path,value):
    if not isinstance(path,str) or not path.startswith('/') or path.split('/')[1] not in UNIT_KEYS-{'unit_id'}:raise ValueError('patch_field_not_allowed')
    seg=path[1:].split('/');cur=u
    for x in seg[:-1]:
        if isinstance(cur,dict):cur=cur.setdefault(x,{})
        elif isinstance(cur,list) and x.isdigit():cur=cur[int(x)]
        else:raise ValueError('patch_parent_not_object')
    if isinstance(cur,dict):cur[seg[-1]]=copy.deepcopy(value)
    elif isinstance(cur,list) and seg[-1].isdigit():cur[int(seg[-1])]=copy.deepcopy(value)
    else:raise ValueError('patch_target_not_addressable')


def initialize(state_json,representation_json):
    s=state_load(state_json);call=s.get('call') or {};mode=call.get('mode');changed=[];accepted=[];rejected=[]
    try:
        d=parse(s,representation_json,mode)
        if mode=='extract':
            pending=[];d=root_fields(d,{'units','source_notes'},{'units','source_notes'},pending,'extract');rows=owned_units(d['units'],s['source'],'f',pending);rejected+=retain_unmodeled(s,pending)
            if not rows:raise ValueError('extract_nonempty_required')
            s['units']=rows;s['extraction_done']=True;s['extraction_notes']=copy.deepcopy(bounded_list(d['source_notes'],50))
            for u in rows:s['revisions'][u['unit_id']]=1
            changed=[u['unit_id'] for u in rows];accepted=changed;ensure_tasks(s)
            review_mode='source_compare';check_refs=[u['unit_id'] for u in s['inventory']+s['units']]
        elif mode=='patch_fact':
            only(d,{'patches','unresolved'},{'patches','unresolved'})
            for p in bounded_list(d['patches'],30):
                try:
                    only(p,{'issue_ref','target_ref','expected_revision','changes','new_unit','evidence_refs','reason'},{'issue_ref','target_ref','expected_revision','changes','new_unit','evidence_refs','reason'})
                    iss=next((i for i in s['issues'] if i['issue_ref']==p['issue_ref'] and i['issue_ref'] in call['issue_refs']),None)
                    if iss is None or iss['required_action']!='patch_fact':raise ValueError('patch_requires_current_source_issue')
                    evidence(s,p['evidence_refs']);uid=p['target_ref'];source_patch_target(s,iss,uid)
                    if uid is None:
                        if p['expected_revision'] is not None or p['changes'] or iss['kind']!='source_omission' or p['new_unit'] is None:raise ValueError('new_fact_requires_omission_issue')
                        u=copy.deepcopy(p['new_unit']);u['unit_id']='f'+str(max([int(x['unit_id'][1:]) for x in s['units']]+[0])+1);u=unit(u,s['source']);validate_links(s['units']+[u]);s['units'].append(u);uid=u['unit_id'];s['revisions'][uid]=1
                        s['changes'].append({'issue_ref':iss['issue_ref'],'target_ref':uid,'before_revision':0,'before':None,'after':copy.deepcopy(u),'independently_confirmed_offline':False})
                    else:
                        u0=units_by_id(s).get(uid)
                        if u0 is None or p['expected_revision']!=s['revisions'][uid] or p['new_unit'] is not None:raise ValueError('patch_expected_revision_mismatch')
                        if uid!=iss['target_ref'] and uid not in s['source_checks'].get(iss['target_ref'],{}).get('fact_refs',[]):raise ValueError('patch_not_issue_target_or_dependency')
                        u=copy.deepcopy(u0)
                        for ch in bounded_list(p['changes'],30):only(ch,{'field_path','value'},{'field_path','value'});set_field(u,ch['field_path'],ch['value'])
                        u=unit(u,s['source']);validate_links([u if x['unit_id']==uid else x for x in s['units']])
                        if u==u0:raise ValueError('patch_no_actual_change')
                        s['changes'].append({'issue_ref':iss['issue_ref'],'target_ref':uid,'before_revision':s['revisions'][uid],'before':copy.deepcopy(u0),'after':copy.deepcopy(u),'independently_confirmed_offline':False})
                        s['units'][s['units'].index(u0)]=u;s['revisions'][uid]+=1
                    changed.append(uid);iss['status']='patched_pending_review';iss['patched_refs']=[uid];accepted.append(uid)
                except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append({'code':str(e),'proposal':p})
            for raw in bounded_list(d['unresolved'],30):
                only(raw,{'issue_ref','reason'},{'issue_ref','reason'})
                iss=next((i for i in s['issues'] if i['issue_ref']==raw['issue_ref'] and i['issue_ref'] in call['issue_refs']),None)
                if iss is None:raise ValueError('unresolved_issue_unknown')
                iss['status']='unresolved';iss['unresolved_reason']=raw['reason']
            affected_source=[r for r,row in s['source_checks'].items() if r in changed or any(x in changed for x in row.get('fact_refs',[]))]
            invalidate(s,changed);review_mode='source_delta';check_refs=list(dict.fromkeys(changed+affected_source+[i['target_ref'] for i in s['issues'] if i.get('patched_refs') and any(x in changed for x in i['patched_refs'])]))
        else:raise ValueError('decompose_mode_owned_by_code')
        log(s,mode,accepted,rejected)
        if check_refs:set_review(s,review_mode,check_refs)
        else:s['call']=None
    except (ValueError,TypeError,KeyError,IndexError) as e:log(s,mode or 'decompose',accepted,rejected+[{'code':str(e)}]);s['call']=None
    return dispatch_output(s,'code_only')


def set_review(s,mode,check_refs):
    if mode.startswith('mapping'):
        check_refs=[b['binding_ref'] for m in s['mappings'] if m['mapping_ref'] in check_refs for b in m['bindings'] if b.get('status')!='invalidated']
    known=refs(s);targets=[{'target_ref':r,'revision':known[r]['revision']} for r in dict.fromkeys(check_refs) if r in known]
    if not targets:s['call']=None;return
    fingerprint=sha({'mode':mode,'targets':targets})
    if fingerprint in s['review_attempts']:
        log(s,'review_not_repeated',rejected=[{'code':'same_current_targets_already_reviewed_or_failed'}]);s['call']=None;return
    s['review_attempts'].append(fingerprint);s['call']={'mode':mode,'targets':targets,'fingerprint':fingerprint}


def query_fingerprint(t,term):return sha({'target':t['target_ref'],'value':t['value_sha256'],'term':term.casefold()})


def query_item_error(error,path,row):
    ref=row.get('target_ref') if isinstance(row,dict) and isinstance(row.get('target_ref'),str) else None
    out=plan_rejection(error,row,[ref] if ref is not None else [])
    out.update(field_path=getattr(error,'field_path',path),target_ref=ref,semantic_repair_claimed=False)
    return out


def receipted_Code_original(s,t,term):
    return [q for q in s['queries'] if q.get('target_ref')==t['target_ref'] and q.get('variant_kind')=='original' and q.get('value_sha256')==t['value_sha256'] and q.get('fingerprint')==query_fingerprint(t,term) and q.get('term')==term and isinstance(q.get('receipt_ref'),str) and re.fullmatch(r'b[1-9][0-9]*',q['receipt_ref']) and int(q['receipt_ref'][1:])<=s['usage']['query_batches_reserved'] and q.get('zero_hit_is_not_absence') is True and q.get('exhaustive') is False and isinstance(q.get('actual_stats'),dict) and q['actual_stats'].get('term')==term]


def plan_queries(s,call,d):
    only(d,{'queries','unresolved','meaning_requests'},{'queries','unresolved'})
    accepted=[];rejected,items=stage_meaning_requests(s,call,d);valid=[];proposed_targets=set();unresolved=[]
    try:query_rows=bounded_list(d['queries'],100,'/queries')
    except (ValueError,TypeError,KeyError,IndexError) as e:query_rows=[];rejected.append(query_item_error(e,'/queries',None))
    for ix,row in enumerate(query_rows):
        path='/queries/'+str(ix)
        try:
            selection_object(row,{'target_ref','query','variant_kind','purpose'},{'target_ref','query','variant_kind','purpose'},path)
            if not isinstance(row['target_ref'],str) or row['target_ref'] not in call['target_refs']:raise ContractError('query_target_not_dispatched',path+'/target_ref','a current dispatched target_ref',row['target_ref'])
            t=task_by_ref(s,row['target_ref']);proposed_targets.add(t['target_ref'])
            if not isinstance(row['query'],str) or not 1<=len(row['query'].strip())<=100:raise ContractError('query_term_range',path+'/query','nonempty string; max 100 characters',row['query'])
            if row['purpose']!=t['purpose']:raise ContractError('query_purpose_target_mismatch',path+'/purpose','the dispatched target purpose '+t['purpose'],row['purpose'])
            q=copy.deepcopy(row);q['query']=row['query'].strip()
            if isinstance(t['value'],str) and q['query']==t['value'].strip():
                proof=receipted_Code_original(s,t,q['query'])
                code_owned=[x for x in call.get('original_queries',[]) if x.get('target_ref')==t['target_ref'] and x.get('variant_kind')=='original' and isinstance(x.get('query'),str) and x['query'].strip()==q['query']]
                if not proof and not code_owned:raise ContractError('model_original_query_not_authorized',path+'/query','original terms require an actual Code receipt or current Code-owned original reservation',row['query'])
                items.append({'field_path':path,'target_ref':t['target_ref'],'status':'redundant','code':'redundant_receipted_Code_original' if proof else 'redundant_Code_owned_original','query_ids':[x['query_id'] for x in proof],'receipt_refs':sorted({x['receipt_ref'] for x in proof}),'semantic_repair_claimed':False});continue
            if row['variant_kind']=='original':raise ContractError('model_original_query_not_authorized',path+'/variant_kind','original is Code-owned and must be the actual original target value',row['variant_kind'])
            if row['variant_kind'] not in ('synonym','english_term','decompose_phrase','broader_query'):raise ContractError('query_variant_not_allowed',path+'/variant_kind','synonym/english_term/decompose_phrase/broader_query',row['variant_kind'])
            valid.append((path,q,query_fingerprint(t,q['query'])))
        except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append(query_item_error(e,path,row))
    queries=copy.deepcopy(call.get('original_queries',[]))+[q for _,q,_ in valid]
    reservation_error=None
    if queries:
        try:reserve_query(s,queries)
        except (ValueError,TypeError,KeyError,IndexError) as e:reservation_error=str(e);rejected.append(query_item_error(e,'/queries',None))
    if not s['pending_query']:reserve_meaning(s)
    pending=s['pending_query'];reserved={q['fingerprint']:q for q in (pending or {}).get('tasks',[])};existing={q['fingerprint']:q for q in s['queries']};item_seen=set()
    for path,q,fp in valid:
        if fp in item_seen or fp in existing:
            item={'field_path':path,'target_ref':q['target_ref'],'status':'redundant','code':'redundant_query_in_batch' if fp in item_seen else 'redundant_receipted_query','semantic_repair_claimed':False}
        elif fp in reserved:
            item={'field_path':path,'target_ref':q['target_ref'],'status':'reserved','code':'query_reserved','query_id':reserved[fp]['query_id'],'batch_id':pending['batch_id'],'semantic_repair_claimed':False}
        else:
            item={'field_path':path,'target_ref':q['target_ref'],'status':'not_executed','code':reservation_error or 'query_term_budget_not_reserved','semantic_repair_claimed':False};rejected.append(copy.deepcopy(item))
        items.append(item);item_seen.add(fp)
    meaning_reserved={row['target_ref'] for row in s.get('meaning_requests',[]) if row['request_ref'] in (pending or {}).get('meaning_request_refs',[])}
    meaning_queued={row['target_ref'] for row in s.get('meaning_requests',[]) if row['status']=='queued' and any(item.get('request_ref')==row['request_ref'] for item in items)}
    actually_reserved={q['target_ref'] for q in (pending or {}).get('tasks',[])}|meaning_reserved;actually_expanded={q['target_ref'] for q in (pending or {}).get('tasks',[]) if q['variant_kind']!='original'};explicit_unresolved=set()
    try:unresolved_rows=bounded_list(d['unresolved'],100,'/unresolved')
    except (ValueError,TypeError,KeyError,IndexError) as e:unresolved_rows=[];rejected.append(query_item_error(e,'/unresolved',None))
    for ix,row in enumerate(unresolved_rows):
        path='/unresolved/'+str(ix)
        try:
            selection_object(row,{'target_ref','reason'},{'target_ref','reason'},path)
            if not isinstance(row['target_ref'],str) or row['target_ref'] not in call['target_refs']:raise ContractError('query_unresolved_target_not_dispatched',path+'/target_ref','a current dispatched target_ref',row['target_ref'])
            if not isinstance(row['reason'],str) or not 1<=len(row['reason'].strip())<=600:raise ContractError('query_short_reason_required',path+'/reason','nonempty string; max 600 characters',row['reason'])
            if row['target_ref'] in actually_reserved:raise ContractError('query_unresolved_conflicts_with_reserved_query',path+'/target_ref','cannot undo an actually reserved query',row['target_ref'])
            t=task_by_ref(s,row['target_ref']);t['status']='unresolved';t['expansion_attempted']=True;t['unresolved_reason']=row['reason'];t.pop('expansion_feedback',None);explicit_unresolved.add(t['target_ref']);items.append({'field_path':path,'target_ref':t['target_ref'],'status':'unresolved','code':'explicit_unresolved','semantic_repair_claimed':False})
        except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append(query_item_error(e,path,row))
    if pending:accepted.extend(q['query_id'] for q in pending['tasks']);accepted.extend(pending.get('meaning_request_refs',[]))
    else:
        for i in s['issues']:
            if i['issue_ref'] in call.get('issue_refs',[]) and i['required_action']=='retrieve':i['status']='unresolved';i['unresolved_reason']='meaning_query_budget_exhausted' if meaning_queued else 'no_executable_query_proposed'
    for ref in call['target_refs']:
        try:t=task_by_ref(s,ref)
        except ValueError as e:rejected.append({'code':str(e),'target_ref':ref,'semantic_repair_claimed':False});continue
        if ref in actually_expanded or ref in explicit_unresolved:t['expansion_attempted']=True;t.pop('expansion_feedback',None)
        elif ref in meaning_reserved:t['expansion_feedback']={'code':'meaning_request_reserved','target_ref':ref,'batch_id':pending['batch_id'],'semantic_repair_claimed':False}
        elif ref in actually_reserved:t['expansion_feedback']={'code':'Code_original_reserved_not_expansion','target_ref':ref,'semantic_repair_claimed':False}
        elif ref in meaning_queued:t['expansion_feedback']={'code':'meaning_request_queued_after_search' if pending else 'meaning_query_budget_exhausted','target_ref':ref,'semantic_repair_claimed':False}
        else:
            rows=[copy.deepcopy(x) for x in rejected+items if x.get('target_ref')==ref]
            feedback={'code':'query_target_not_executed' if ref in proposed_targets else 'query_target_not_submitted','target_ref':ref,'items':rows,'semantic_repair_claimed':False};t['expansion_feedback']=copy.deepcopy(feedback);rejected.append(feedback)
    s['call']=None
    info={'query_outcome':'meaning_reserved' if pending and pending.get('mode')=='meaning' else 'reserved' if pending else 'meaning_query_budget_exhausted' if meaning_queued else 'no_executable_query_proposed','new_query_reserved':bool(pending),'issue_refs':call.get('issue_refs',[]),'query_items':items,'semantic_repair_claimed':False}
    return accepted,rejected,info


def meaning_refs(s,candidate_ref):
    cid=s['candidate_refs'].get(candidate_ref);c=s['catalog'].get(str(cid),{});out=[]
    for ref in c.get('meaning_evidence_refs',[]):
        b=s.get('meaning_evidence',{}).get(ref);source=(b or {}).get('source') or {}
        if b and b.get('concept_id')==cid and b.get('availability',{}).get('concept_identity')=='available' and all(source.get(k)==c.get(k) for k in CONCEPT_FIELDS+('vocabulary_version',)):out.append(ref)
    return out


def retain_meaning_evidence(s,rows,date,allowed_ids,receipt=None):
    retained=[];rejected=[]
    for ix,raw in enumerate(bounded_list(rows,6)):
        try:
            if not isinstance(raw,dict) or type(raw.get('concept_id')) is not int or raw['concept_id'] not in allowed_ids:raise ValueError('meaning_concept_not_in_actual_request_or_receipt')
            ref='meaning:'+hashlib.sha256(json.dumps({k:v for k,v in raw.items() if k!='meaning_ref'},sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:24]
            if raw.get('meaning_ref')!=ref or raw.get('schema_version')!='ohdsi_meaning_e4_v1' or raw.get('verification_date')!=date:raise ValueError('meaning_actual_ref_schema_or_date_mismatch')
            availability=raw.get('availability',{})
            if any(availability.get(k)!='not_stored_in_source_schema' for k in ('description_type','formal_text_definition','relationship_group')):raise ValueError('meaning_cannot_invent_typed_description_definition_or_role_group')
            c=s['catalog'].get(str(raw['concept_id']));source=raw.get('source')
            if availability.get('concept_identity')=='available':
                if not c or not isinstance(source,dict) or not set(CONCEPT_FIELDS+('vocabulary_version',))<=set(source) or any(source[k]!=c.get(k) for k in CONCEPT_FIELDS+('vocabulary_version',)):raise ValueError('meaning_source_identity_version_mismatch')
            elif availability.get('concept_identity')!='not_found' or source is not None:raise ValueError('meaning_identity_availability_contract')
            for synonym in bounded_list(raw.get('synonyms'),12):
                if synonym.get('concept_id')!=raw['concept_id'] or not isinstance(synonym.get('term'),str) or not synonym['term'] or synonym.get('source_table')!='concept_synonym' or synonym.get('description_type')!='not_stored_in_source_schema' or type(synonym.get('language_concept_id')) is not int:raise ValueError('meaning_synonym_actual_source_contract')
            for relation in bounded_list(raw.get('relations'),10):
                if relation.get('source_id')!=raw['concept_id'] or relation.get('direction')!='source_to_target' or not isinstance(relation.get('relationship_id'),str) or (relation.get('target_identity') or {}).get('concept_id')!=relation.get('target_id'):raise ValueError('meaning_relation_actual_direction_identity')
            s.setdefault('meaning_evidence',{})[ref]=copy.deepcopy(raw)
            if receipt is not None:
                provenance={k:copy.deepcopy(receipt.get(k)) for k in ('batch_id','mode','input_request','verification_date')}
                history=s.setdefault('meaning_provenance',{}).setdefault(ref,[])
                if provenance not in history:history.append(provenance)
            if c:
                c.setdefault('meaning_evidence_refs',[])
                if ref not in c['meaning_evidence_refs']:c['meaning_evidence_refs'].append(ref)
            retained.append(ref)
        except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append({'code':str(e),'field_path':'/meaning_evidence/'+str(ix),'concept_id':raw.get('concept_id') if isinstance(raw,dict) else None})
    return retained,rejected


def queue_meaning(s,target_ref,candidate_ref,reason,issue_refs=None,origin='model_request'):
    t=task_by_ref(s,target_ref)
    if candidate_ref not in actual_candidates(s,t):raise ValueError('meaning_candidate_not_in_actual_target_pool')
    if meaning_refs(s,candidate_ref):return None,'meaning_evidence_already_available'
    cid=s['candidate_refs'][candidate_ref];prior=next((x for x in s.get('meaning_requests',[]) if x['target_ref']==target_ref and x['candidate_ref']==candidate_ref and x['value_sha256']==t['value_sha256']),None)
    if prior:
        if prior['status'] in ('queued','pending'):
            prior['issue_refs']=list(dict.fromkeys(prior.get('issue_refs',[])+list(issue_refs or [])));return prior,'meaning_request_already_queued'
        raise ValueError('meaning_already_looked_up_without_current_available_bundle')
    row={'request_ref':'mr'+str(len(s.setdefault('meaning_requests',[]))+1),'target_ref':target_ref,'candidate_ref':candidate_ref,'concept_id':cid,'unit_id':t['unit_id'],'revision':t['revision'],'value_sha256':t['value_sha256'],'reason':reason,'issue_refs':list(issue_refs or []),'origin':origin,'status':'queued'}
    s['meaning_requests'].append(row)
    if t['status'] not in ('selected','unresolved'):t['status']='awaiting_meaning'
    return row,'meaning_request_queued'


def stage_meaning_requests(s,call,d):
    rejected=[];items=[]
    for ix,row in enumerate(bounded_list(d.get('meaning_requests',[]),100,'/meaning_requests')):
        path='/meaning_requests/'+str(ix)
        try:
            selection_object(row,{'target_id','candidate_ref','reason'},{'target_id','candidate_ref','reason'},path)
            if row['target_id'] not in call.get('target_refs',[]):raise ValueError('meaning_target_not_current_dispatched_task')
            if not isinstance(row['reason'],str) or not 1<=len(row['reason'].strip())<=240:raise ValueError('meaning_short_reason_required')
            request,code=queue_meaning(s,row['target_id'],row['candidate_ref'],row['reason'],call.get('issue_refs',[]))
            items.append({'field_path':path,'target_ref':row['target_id'],'candidate_ref':row['candidate_ref'],'code':code,'request_ref':request['request_ref'] if request else None,'available_refs':meaning_refs(s,row['candidate_ref']),'semantic_repair_claimed':False})
        except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append(query_item_error(e,path,dict(row,target_ref=row.get('target_id')) if isinstance(row,dict) else row))
    return rejected,items


def reserve_meaning(s):
    if s['usage']['query_batches_reserved']>=s['budget']['max_query_batches']:return False
    queued=[];ids=[]
    for row in s.get('meaning_requests',[]):
        if row['status']!='queued':continue
        try:t=task_by_ref(s,row['target_ref'])
        except ValueError:row['status']='stale';continue
        if row['revision']!=t['revision'] or row['value_sha256']!=t['value_sha256']:row['status']='stale';continue
        if meaning_refs(s,row['candidate_ref']):row['status']='satisfied_by_existing_receipt';continue
        if row['concept_id'] not in ids:
            if len(ids)>=6:continue
            ids.append(row['concept_id'])
        queued.append(row)
    if not queued:return False
    s['usage']['query_batches_reserved']+=1;batch='b'+str(s['usage']['query_batches_reserved'])
    for row in queued:row['status']='pending';row['batch_id']=batch
    request={'mode':'meaning','concept_ids':ids}
    s['pending_query']={'batch_id':batch,'mode':'meaning','request':request,'terms':[],'concept_ids':ids,'tasks':[],'meaning_request_refs':[x['request_ref'] for x in queued],'issue_refs':list(dict.fromkeys(i for x in queued for i in x.get('issue_refs',[])))};s['call']=None
    return True


def apply_meaning_receipt(s,r):
    p=s['pending_query'];rejected=[];retained=[]
    try:
        if r.get('batch_id')!=p['batch_id'] or r.get('mode')!='meaning' or r.get('input_request')!=p['request']:raise ValueError('meaning_receipt_pending_identity_mismatch')
        if r.get('safety',{}).get('transaction_read_only')!='on' or r.get('error'):raise ValueError('meaning_readonly_or_query_failure')
        if any(r.get(k) for k in ('concepts','relationships','retrieval_evidence','term_results')):raise ValueError('meaning_receipt_cannot_change_lexical_candidate_pool')
        date=datetime.date.fromisoformat(r['verification_date']).isoformat();retained,rejected=retain_meaning_evidence(s,r.get('meaning_evidence',[]),date,set(p['concept_ids']),r)
        bycid={s['meaning_evidence'][ref]['concept_id'] for ref in retained if s['meaning_evidence'][ref]['availability']['concept_identity']=='available'}
        for request in s.get('meaning_requests',[]):
            if request['request_ref'] not in p['meaning_request_refs']:continue
            request['receipt_ref']=p['batch_id'];request['meaning_evidence_refs']=[ref for ref in retained if s['meaning_evidence'][ref]['concept_id']==request['concept_id']]
            request['status']='received' if request['concept_id'] in bycid else 'unavailable_after_receipt'
            if request['status']=='received':
                t=task_by_ref(s,request['target_ref']);t['status']='not_selected';t.pop('unresolved_reason',None)
                for issue in s['issues']:
                    if issue['issue_ref'] not in request['issue_refs']:continue
                    issue['status']='retrieved_pending_reselection';issue.pop('unresolved_reason',None);issue['reselection_target_refs']=list(dict.fromkeys(issue.get('reselection_target_refs',[])+[request['target_ref']]))
                    issue.setdefault('retrieval_receipts',[]).append({'receipt_ref':p['batch_id'],'query_ids':[],'meaning_request_refs':[request['request_ref']],'meaning_evidence_refs':request['meaning_evidence_refs']})
            else:
                for issue in s['issues']:
                    if issue['issue_ref'] in request['issue_refs']:issue['status']='unresolved';issue['unresolved_reason']='meaning_unavailable_after_actual_receipt'
        log(s,'meaning_retrieve',accepted=retained,rejected=rejected,info={'batch_id':p['batch_id'],'actual_requested_concept_ids':p['concept_ids'],'semantic_repair_claimed':False})
    except (ValueError,TypeError,KeyError,IndexError) as e:
        for request in s.get('meaning_requests',[]):
            if request['request_ref'] in p['meaning_request_refs']:request['status']='failed';request['failure']=str(e)
        for issue in s['issues']:
            if issue['issue_ref'] in p.get('issue_refs',[]):issue['status']='unresolved';issue['unresolved_reason']='meaning_retrieval_failed:'+str(e)
        log(s,'meaning_retrieve',rejected=[{'code':str(e)}],info={'semantic_repair_claimed':False})
    s['pending_query']=None;s['call']=None;return output(s)


def reserve_query(s,queries):
    if s['usage']['query_batches_reserved']>=s['budget']['max_query_batches']:raise ValueError('query_batch_budget_exhausted')
    pending=[];terms=[];seen={q['fingerprint'] for q in s['queries']}
    for q in queries:
        t=task_by_ref(s,q['target_ref']);term=q['query'].strip()
        if not term or len(term)>100:raise ValueError('query_term_range')
        fp=query_fingerprint(t,term)
        if fp in seen:continue
        seen.add(fp)
        if term not in terms:
            if len(terms)>=s['budget']['max_terms_per_batch']:continue
            terms.append(term)
        pending.append({'query_id':'q'+str(len(s['queries'])+len(pending)+1),'target_ref':t['target_ref'],'term':term,'variant_kind':q['variant_kind'],'purpose':q['purpose'],'unit_id':t['unit_id'],'field_path':t['field_path'],'revision':t['revision'],'value_sha256':t['value_sha256'],'fingerprint':fp})
    if not pending:raise ValueError('no_new_executable_queries')
    issue_refs=[i['issue_ref'] for i in s['issues'] if i['issue_ref'] in (s.get('call') or {}).get('issue_refs',[]) and i['required_action']=='retrieve']
    s['usage']['query_batches_reserved']+=1;s['pending_query']={'batch_id':'b'+str(s['usage']['query_batches_reserved']),'mode':'search','request':{'mode':'search','terms':terms},'terms':terms,'tasks':pending,'issue_refs':issue_refs};s['call']=None
    return True


def expansion_call(s,targets,raw_core):
    targets=sorted(targets,key=lambda t:(retrieval_assessment(s,t)['eligible_standard_count']>0,{'core':0,'component':1,'site':2}.get(t['purpose'],3),t['target_ref']))
    cap=s['budget']['max_terms_per_batch'];original=raw_core[:cap//2]
    reserved=[{'target_ref':t['target_ref'],'query':t['value'],'variant_kind':'original','purpose':t['purpose']} for t in original]
    slots=max(1,cap-len({x['query'] for x in reserved}))
    s['call']={'mode':'query_only','target_refs':[t['target_ref'] for t in targets[:slots]],'issue_refs':[], 'original_queries':reserved,'query_slots_available':slots}
    return 'planner'


def source_prerequisite_blockers(s):
    pending=[]
    for u in s['inventory']+s['units']:
        ref=u['unit_id'];check=s['source_checks'].get(ref);revision=s['revisions'][ref]
        if not check or check.get('revision')!=revision or not check.get('model_checked_current_revision'):
            pending.append({'target_ref':ref,'revision':revision,'reason':'current_source_check_missing'})
        elif check.get('status')=='missing':pending.append({'target_ref':ref,'revision':revision,'reason':'source_information_missing'})
    if not s['inventory_complete']:pending.append({'reason':'blind_inventory_incomplete'})
    for row in s.get('unmodeled',[]):
        if row.get('status')=='unmodeled_pending':pending.append({'pending_ref':row['pending_ref'],'target_ref':row['unit_ref'],'field_path':row['field_path'],'reason':'unmodeled_pending','origin':row['origin']})
    for issue in s['issues']:
        if issue['required_action']=='patch_fact' and issue['status']!='model_checked_repair_pending_offline':pending.append({'issue_ref':issue['issue_ref'],'target_ref':issue['target_ref'],'reason':'source_issue_not_closed','status':issue['status']})
    return pending

def retrieval_reselection_issues(s,target_refs):
    wanted=set(target_refs)
    return [i for i in s['issues'] if i['required_action']=='retrieve' and i['status']=='retrieved_pending_reselection' and wanted&set(i.get('reselection_target_refs',[]))]

def mapping_reselection_issues(s,target_refs):
    return [i for i in s['issues'] if i['status']=='open' and i['required_action'] in ('replace_mapping','revise_rationale','calibrate_relation') and
            set((binding_owner(s,i['target_ref'])[1] or {}).get('covers',[]))&set(target_refs)]

def completion_blockers(s):
    out=[]
    if s['pending_query']:out.append({'kind':'pending_retrieval_receipt','batch_id':s['pending_query']['batch_id']})
    if s.get('call'):out.append({'kind':'pending_model_or_review_action','mode':s['call']['mode']})
    for row in s.get('decision_queue',{}).values():
        if row['status'] in ('pending','pending_omitted','pending_rejected','exhausted_pending'):
            out.append({'kind':'target_decision_pending','target_ref':row['target_ref'],'revision':row['revision'],'queue_key':row['queue_key'],'status':row['status']})
    for key,row in s.get('mapping_contract_feedback',{}).items():
        if row['status']!='corrected_pending_offline':out.append({'kind':'mapping_review_contract_pending','key':key,'mapping_ref':row['mapping_ref'],'status':row['status'],'attempts':row['attempts']})
    for request in s.get('meaning_requests',[]):
        if request['status'] in ('queued','pending'):out.append({'kind':'pending_meaning_request','request_ref':request['request_ref'],'status':request['status']})
    for i in s['issues']:
        if s.get('explanation_contract')=='typed_assertions_e8' and i['kind'] in RATIONALE_KINDS:continue
        if i['status']!='model_checked_repair_pending_offline':out.append({'kind':'unresolved_feedback','issue_ref':i['issue_ref'],'status':i['status']})
    for m in s['mappings']:
        if any(b.get('status')!='invalidated' for b in m['bindings']) and s.get('explanation_contract')!='typed_assertions_e8' and m.get('reason_status') not in (None,'supported_pending_offline'):out.append({'kind':'unverified_auxiliary_reason','mapping_ref':m['mapping_ref'],'status':m['reason_status']})
        for b in m['bindings']:
            if b.get('status')=='invalidated':continue
            check=s['mapping_checks'].get(b.get('binding_ref'))
            if b.get('status','pending_review')=='pending_review' or not check or check.get('revision')!=b.get('revision'):
                out.append({'kind':'binding_review_not_current','binding_ref':b.get('binding_ref'),'revision':b.get('revision')})
    return out


def mapping_contract_key(s,binding):
    return sha({'source':s['source']['source_sha256'],'binding':binding_semantics(binding),'actual_evidence_generation':issue_evidence_generation(s,binding['binding_ref'])})


def queue_mapping_contract_feedback(s,call,errors,mapping_refs,view,review_ref):
    ledger=s.setdefault('mapping_contract_feedback',{});requested={row['target_ref'] for row in call['targets']}
    obligations={row['assertion_ref']:row['binding_ref'] for row in view.get('assertion_obligations',[])};failures={}
    for error in errors:
        proposal=error.get('proposal') if isinstance(error.get('proposal'),dict) else {}
        target=error.get('target_ref') or proposal.get('target_ref');assertion=error.get('assertion_ref') or proposal.get('assertion_ref')
        target=obligations.get(assertion,target)
        affected={target} if isinstance(target,str) and target in requested else requested if not target and not assertion else set()
        for ref in affected:failures.setdefault(ref,[]).append(error)
    for key in call.get('mapping_contract_retry_keys',[]):
        if key in ledger:
            row=ledger[key];row['status']='exhausted_pending' if row['binding_ref'] in failures else 'corrected_pending_offline';row['actual_retry_review_ref']=review_ref
    for ref,actual_errors in failures.items():
        mapping,binding=binding_owner(s,ref)
        if mapping is None or mapping['mapping_ref'] not in mapping_refs:continue
        key=mapping_contract_key(s,binding)
        row=ledger.setdefault(key,{'key':key,'mapping_ref':mapping['mapping_ref'],'binding_ref':ref,'revision':binding['revision'],'attempts':0,'status':'pending','semantic_repair_claimed':False})
        row.update(actual_errors=copy.deepcopy(actual_errors),source_sha256=s['source']['source_sha256'],evidence_generation=issue_evidence_generation(s,ref),
                   evidence_snapshot_sha256=view.get('evidence_snapshot_sha256'),last_review_ref=review_ref)
        row['status']='exhausted_pending' if row['attempts'] else 'pending'


def schedule_mapping_contract_retry(s):
    eligible=[];targets=[]
    for key,row in s.get('mapping_contract_feedback',{}).items():
        if row['status']!='pending' or row['attempts']>=1:continue
        mapping,binding=binding_owner(s,row.get('binding_ref'))
        if binding is None or binding.get('status')=='invalidated' or binding['revision']!=row['revision'] or mapping_contract_key(s,binding)!=key:
            row['status']='stale_pending';continue
        if any(i['target_ref']==binding['binding_ref'] and i['kind'] in MAPPING_KINDS and not i.get('evidence_gate_only') and
               (not i.get('calibration_only') or i.get('strict_direction_negative')) and i['status']!='model_checked_repair_pending_offline' for i in s['issues']):
            row['status']='semantic_feedback_priority';continue
        row['attempts']+=1;row['status']='retry_dispatched';eligible.append(key);targets.append({'target_ref':binding['binding_ref'],'revision':binding['revision']})
    if not eligible:return False
    s['call']={'mode':'mapping_delta','targets':targets,'mapping_contract_retry_keys':eligible,'fingerprint':sha({'mapping_contract_retry_keys':eligible}),'contract_retry':True}
    return True


def schedule(s):
    if s['stop']:return 'code_only'
    if s['usage']['loop_waves']>=s['budget']['max_loop_waves']:
        s['stop']=True;s['stop_reason']='loop_wave_budget_exhausted_with_explicit_residuals';return 'code_only'
    s['usage']['loop_waves']+=1
    if not s['extraction_done']:s['call']={'mode':'extract'};return 'decompose'
    if s.get('call') and s['call']['mode'] in ('source_compare','source_delta','mapping_verify','mapping_delta'):return 'code_only'
    open_source=[i for i in s['issues'] if i['status']=='open' and i['required_action']=='patch_fact' and i['attempts']==0]
    if open_source and s['usage']['repair_waves']<s['budget']['max_repair_waves']:
        s['usage']['repair_waves']+=1
        for i in open_source:i['attempts']+=1
        s['call']={'mode':'patch_fact','issue_refs':[i['issue_ref'] for i in open_source]};return 'decompose'
    for i in open_source:i['status']='unresolved';i['unresolved_reason']='source_repair_budget_exhausted'
    blockers=source_prerequisite_blockers(s)
    if blockers:
        s['source_gate']={'status':'incomplete','blockers':blockers,'semantic_repair_claimed':False};s['stop']=True;s['stop_reason']='source_prerequisite_incomplete';s['call']=None
        log(s,'source_prerequisite',rejected=blockers,info={'new_query_reserved':False,'safe_stop_is_not_semantic_repair':True});return 'code_only'
    s['source_gate']={'status':'checks_current','blockers':[],'semantic_repair_claimed':False}
    if s['pending_query']:return 'code_only'
    ensure_tasks(s);current=[t for t in s['tasks'] if target_current(s,t)]
    if schedule_mapping_contract_retry(s):return 'code_only'
    reference_retry=[]
    for ref,row in s.get('meaning_reference_feedback',{}).items():
        m,b=binding_owner(s,ref)
        if row['attempts']<1 and b is not None and b['revision']==row['revision'] and meaning_refs(s,b['candidate_ref']):reference_retry.append(ref);row['attempts']+=1
    if reference_retry:
        targets=[{'target_ref':ref,'revision':binding_owner(s,ref)[1]['revision']} for ref in reference_retry]
        fingerprint=sha({'mode':'mapping_delta','targets':targets,'meaning_reference_retry':True})
        if fingerprint not in s['review_attempts']:
            s['review_attempts'].append(fingerprint);s['call']={'mode':'mapping_delta','targets':targets,'fingerprint':fingerprint,'meaning_reference_retry':True};return 'code_only'
    missing=[i for i in s['issues'] if i['status']=='open' and i['kind']=='meaning_evidence_missing' and i['attempts']==0]
    for issue in missing:
        issue['attempts']+=1;m,b=binding_owner(s,issue['target_ref'])
        if b is None:issue['status']='unresolved';issue['unresolved_reason']='meaning_binding_not_current';continue
        for target in b['covers']:
            try:queue_meaning(s,target,b['candidate_ref'],issue['detail'],[issue['issue_ref']],issue.get('origin','model_issue'))
            except (ValueError,TypeError,KeyError,IndexError) as e:issue['status']='unresolved';issue['unresolved_reason']=str(e)
    if reserve_meaning(s):return 'code_only'
    for t in current:
        if t['status']=='contract_retry_pending' and t.get('selection_contract_retry_attempts',0)>=1:
            t['status']='unresolved';t['unresolved_reason']='selection_contract_retry_already_dispatched_without_result'
    retry=[t for t in current if t['status']=='contract_retry_pending' and actual_candidates(s,t)]
    if retry:
        for t in retry:t['selection_contract_retry_attempts']=t.get('selection_contract_retry_attempts',0)+1
        retry_refs=[t['target_ref'] for t in retry]
        s['call']={'mode':'select_only','target_refs':retry_refs,'issue_refs':[i['issue_ref'] for i in mapping_reselection_issues(s,retry_refs)+retrieval_reselection_issues(s,retry_refs)],'contract_retry':True};return 'planner'
    raw=sorted([t for t in current if t['query_status']=='not_attempted' and isinstance(t['value'],str) and len(t['value'])<=100],key=lambda t:({'core':0,'component':1,'site':2}.get(t['purpose'],3),t['target_ref']))
    raw_core=[t for t in raw if t['purpose']=='core']
    qissues=[i for i in s['issues'] if i['status']=='open' and i['required_action']=='retrieve' and i['kind']!='meaning_evidence_missing' and i['attempts']==0]
    if qissues:
        for i in qissues:i['attempts']+=1
        affected={r for i in qissues for m in s['mappings'] for b in m['bindings'] if b.get('binding_ref')==i['target_ref'] for r in b['covers']}
        expanded=[t for t in current if not affected or t['target_ref'] in affected]
        if expanded and s['usage']['query_batches_reserved']<s['budget']['max_query_batches']:
            action=expansion_call(s,expanded,[]);s['call']['issue_refs']=[i['issue_ref'] for i in qissues];return action
    insufficient=[t for t in current if not t['expansion_attempted'] and retrieval_assessment(s,t)['needs_expansion']]
    if insufficient and s['usage']['query_batches_reserved']<s['budget']['max_query_batches']:return expansion_call(s,insufficient,raw_core)
    if raw and s['usage']['query_batches_reserved']<s['budget']['max_query_batches']:
        reserve_query(s,[{'target_ref':t['target_ref'],'query':t['value'],'variant_kind':'original','purpose':t['purpose']} for t in raw]);return 'code_only'
    need_expand=[t for t in current if not actual_candidates(s,t) and not t['expansion_attempted'] and t['status'] not in ('unresolved','selected')]
    if need_expand and s['usage']['query_batches_reserved']<s['budget']['max_query_batches']:
        return expansion_call(s,need_expand,raw_core)
    auxiliary=[i for i in s['issues'] if s.get('explanation_contract')!='typed_assertions_e8' and i.get('auxiliary_only') and i['status']!='model_checked_repair_pending_offline']
    for issue in auxiliary:
        parent,b=binding_owner(s,issue['target_ref'])
        if parent is None or b.get('status') not in ACCEPTED_BINDING_STATUSES:continue
        targets=[{'target_ref':item['binding_ref'],'revision':item['revision']} for item in parent['bindings'] if item.get('status') in ACCEPTED_BINDING_STATUSES]
        prior_call=s.get('call');s['call']={'mode':'mapping_delta','targets':targets}
        current_view=view_for_call(s);s['call']=prior_call
        scope=rationale_scope_digest(current_view,parent['mapping_ref']);key=sha({'binding_semantics':[(item['candidate_ref'],item['role'],item['relation'],sorted(item['covers'])) for item in parent['bindings']],'dependencies':parent['dependencies'],'source':s['source']['source_sha256']})
        attempts=s.setdefault('rationale_evidence_rechecks',{})
        if scope!=issue.get('evidence_scope_sha256') and attempts.get(key,0)<1:
            attempts[key]=attempts.get(key,0)+1;issue['attempts']+=1;issue['status']='evidence_recheck_pending';issue['evidence_scope_sha256']=scope
            s['call']={'mode':'mapping_delta','targets':targets,'fingerprint':sha([key,scope]),'rationale_evidence_recheck':True};return 'code_only'
        issue['status']='unresolved';issue['unresolved_reason']='auxiliary_reason_unverified_no_new_evidence_or_recheck_limit';issue['binding_preserved']=True
    wrong=[i for i in s['issues'] if i['status']=='open' and i['required_action'] in ('replace_mapping','revise_rationale','calibrate_relation') and i['attempts']==0 and (s.get('explanation_contract')!='typed_assertions_e8' or i['kind'] not in RATIONALE_KINDS)]
    for i in wrong:
        i['attempts']+=1
        if i.get('calibration_only'):
            _,current_binding=binding_owner(s,i['target_ref'])
            for target in current_binding['covers']:
                task=task_by_ref(s,target);task['status']='not_selected';task.pop('unresolved_reason',None)
        else:binding_invalidate(s,i['target_ref'],i['issue_ref'])
        if i.get('rationale_mapping_ref'):
            parent=next((m for m in s['mappings'] if m['mapping_ref']==i['rationale_mapping_ref']),None)
            for ref in parent['target_refs'] if parent else []:
                t=task_by_ref(s,ref);t['status']='not_selected';t.pop('unresolved_reason',None)
    selectable=[t for t in current if actual_candidates(s,t) and t['status'] not in ('selected','unresolved')]
    if selectable:
        selected_refs=[t['target_ref'] for t in selectable]
        pending_feedback=[i['issue_ref'] for i in mapping_reselection_issues(s,selected_refs)]
        s['call']={'mode':'select_only','target_refs':selected_refs,'issue_refs':list(dict.fromkeys(pending_feedback+[i['issue_ref'] for i in retrieval_reselection_issues(s,selected_refs)]))};return 'planner'
    s['call']=None;s['stop']=True;s['stop_reason']='stopped_with_unresolved_feedback_or_review' if completion_blockers(s) else 'no_executable_work_remaining_with_explicit_residuals';return 'code_only'


def planner_target_view(s,t):
    keys=('target_ref','unit_id','field_path','revision','value','purpose','status','query_status','candidate_refs','expansion_attempted')
    return dict({k:copy.deepcopy(t[k]) for k in keys},retrieval_assessment=retrieval_assessment(s,t))


def planner_query_view(q):
    keys=('query_id','target_ref','term','variant_kind','purpose','revision','receipt_ref','exhaustive','zero_hit_is_not_absence')
    stats=('cap','term','truncated','term_index','phrase_stage','returned_count','error','error_code','query_ok')
    return dict({k:copy.deepcopy(q[k]) for k in keys if k in q},actual_stats={k:copy.deepcopy(v) for k,v in q['actual_stats'].items() if k in stats})


def planner_mapping_view(m):
    keys=('mapping_ref','revision','status','target_refs','dependencies','residuals','feedback_issue_refs','reason')
    bkeys=('binding_ref','revision','status','candidate_ref','role','relation','relation_origin','relationship_ref','covers')
    return dict({k:copy.deepcopy(m[k]) for k in keys if k in m},bindings=[{k:copy.deepcopy(b[k]) for k in bkeys if k in b} for b in m['bindings']])


def view_for_call(s):
    call=copy.deepcopy(s.get('call') or {'mode':'none'});mode=call['mode'];v={'mode':mode,'budget':budget_view(s),'source':s['source'],'last_feedback':({k:copy.deepcopy(v) for k,v in s['last_feedback'].items() if k!='rejected'}|{'rejected':[{k:copy.deepcopy(v) for k,v in r.items() if k!='proposal'} for r in s['last_feedback'].get('rejected',[])]}) if s['last_feedback'] else None}
    if mode=='extract':return {k:v[k] for k in ('mode','budget','source')}
    if mode=='patch_fact':
        v['issues']=[i for i in s['issues'] if i['issue_ref'] in call['issue_refs']];wanted={i['target_ref'] for i in v['issues']}
        wanted|={r for i in v['issues'] for r in s['source_checks'].get(i['target_ref'],{}).get('fact_refs',[])}
        v['facts']=[fact_public(s,u) for u in s['units'] if u['unit_id'] in wanted];v['obligations']=[fact_public(s,u) for u in s['inventory'] if u['unit_id'] in wanted];return v
    if mode in ('source_compare','source_delta','mapping_verify','mapping_delta'):
        known=refs(s);v['targets']=call['targets'];wanted={r['target_ref'] for r in call['targets']}
        if mode.startswith('source'):
            v['facts']=[fact_public(s,u) for u in s['units']];v['obligations']=[fact_public(s,u) for u in s['inventory'] if mode=='source_compare' or u['unit_id'] in wanted]
            v['extraction_notes']=copy.deepcopy(s.get('extraction_notes',[]));v['blind_source_notes']=copy.deepcopy(s['source_notes']);v['unmodeled_pending']=copy.deepcopy(s.get('unmodeled',[]))
            v['issues']=[i for i in s['issues'] if i['target_ref'] in wanted or any(x in wanted for x in i.get('patched_refs',[]))]
        else:
            maps=[m for m in s['mappings'] if any(b.get('binding_ref') in wanted for b in m['bindings'])]
            v['bindings']=[dict(copy.deepcopy(b),mapping_ref=m['mapping_ref'],dependencies=copy.deepcopy(m['dependencies'])) for m in maps for b in m['bindings'] if b.get('binding_ref') in wanted]
            v['assertion_obligations']=assertion_obligations(s,v['bindings'])
            factids={d['target_ref'] for m in maps for d in m['dependencies']};v['facts']=[fact_public(s,u) for u in s['units'] if u['unit_id'] in factids]
            tids={r for b in v['bindings'] for r in b['covers']}
            v['retrieval_targets']=[dict({k:copy.deepcopy(t[k]) for k in ('target_ref','unit_id','field_path','revision','value','purpose','query_status')},retrieval_assessment=retrieval_assessment(s,t)) for t in s['tasks'] if t['target_ref'] in tids]
            v.update(review_projection(s,v['bindings']));v['meaning_reference_feedback']={ref:copy.deepcopy(s['meaning_reference_feedback'][ref]) for ref in wanted if ref in s.get('meaning_reference_feedback',{})}
            issue_refs={r for m in maps for r in m.get('feedback_issue_refs',[])}
            v['issues']=[copy.deepcopy(i) for i in s['issues'] if i['issue_ref'] in issue_refs]
            v['source_facets']=[f for f in source_facets(s) if f['unit_id'] in factids]
            rationale_candidate_handoff(s,v,maps)
            review_evidence_registry(s,v)
            if call.get('mapping_contract_retry_keys'):v['contract_retry']={'maximum_resubmissions':1,'feedback_refs_scope':'historical_rejected_reply_only; cite the current evidence_rows','semantic_repair_claimed':False,'feedback':[copy.deepcopy(s['mapping_contract_feedback'][key]) for key in call['mapping_contract_retry_keys']],'instruction':'Correct only the rejected targets using current evidence_refs; preserve binding/ref/revision and accepted neighbors. Return the current mapping review contract.'}
        return v
    selected=[task_by_ref(s,r) for r in call.get('target_refs',[])]
    v['targets']=[planner_target_view(s,t) for t in selected];factids={t['unit_id'] for t in selected};v['facts']=[fact_public(s,u) for u in s['units'] if u['unit_id'] in factids]
    if mode=='query_only':v['query_slots_available']=call.get('query_slots_available',s['budget']['max_terms_per_batch']);v['code_reserved_original_queries']=call.get('original_queries',[])
    v['queries']=[planner_query_view(q) for q in s['queries'] if q['target_ref'] in {t['target_ref'] for t in selected}]
    if mode in ('query_only','select_only'):
        v['issues']=[copy.deepcopy(i) for i in s['issues'] if i['issue_ref'] in call.get('issue_refs',[])]
        cids={r for t in selected for r in t['candidate_refs']};v.update(projected_evidence(s,cids,meaning_details=mode!='select_only' or bool(call.get('issue_refs'))));v['source_facets']=[f for f in source_facets(s) if f['unit_id'] in factids];v['prior_mappings']=[planner_mapping_view(m) for m in s['mappings'] if any(r in call['target_refs'] for r in m['target_refs'])]
        factids|={d['target_ref'] for m in v['prior_mappings'] for d in m['dependencies']}
        v['facts']=[fact_public(s,u) for u in s['units'] if u['unit_id'] in factids]
        if mode=='select_only' and call.get('contract_retry'):
            v['contract_retry']={'maximum_resubmissions':1,'feedback':{r:copy.deepcopy(s.get('selection_contract_feedback',{}).get(r,[])) for r in call['target_refs']},'semantic_repair_claimed':False}
        if mode=='select_only':v['decision_queue']=[{key:copy.deepcopy(row[key]) for key in ('queue_key','target_ref','revision','status','gap_keys')} for row in decision_items(s,call)]
    return v


def dispatch_output(s,dispatch='code_only'):
    v=view_for_call(s);pending=s['pending_query'];mode=(s.get('call') or {}).get('mode')
    return dict(output(s),dispatch=dispatch,is_decompose=dispatch=='decompose',is_planner=dispatch=='planner',decompose_json=dump(v) if dispatch=='decompose' else '{}',planner_json=dump(v) if dispatch=='planner' else '{}',reviewer_json=dump(v) if mode in ('source_compare','source_delta','mapping_verify','mapping_delta') else '{}',is_search=bool(pending),terms_json=dump(pending.get('request',pending['terms']) if pending else []),batch_id=pending['batch_id'] if pending else 'none',budget_json=dump(budget_view(s)),action=mode or ('execute_query' if pending else 'finish' if s['stop'] else 'none'),is_review=mode in ('source_compare','source_delta','mapping_verify','mapping_delta'))


def model_view(state_json):
    s=state_load(state_json);s['explanation_contract']='typed_assertions_e8';dispatch=schedule(s);return dispatch_output(s,dispatch)


def plan(state_json,decision_json):
    s=state_load(state_json);s['explanation_contract']='typed_assertions_e8';call=s.get('call') or {};mode=call.get('mode');accepted=[];rejected=[];info={};outcomes={}
    try:
        d=parse(s,decision_json,mode)
        if mode=='query_only':
            accepted,rejected,info=plan_queries(s,call,d)
        elif mode=='select_only':
            selection_object(d,{'selections','unresolved','meaning_requests'},{'selections','unresolved'},'/');new=[]
            meaning_rejected,meaning_items=stage_meaning_requests(s,call,d);rejected.extend(meaning_rejected);info['meaning_requests']=meaning_items
            for ix,row in enumerate(bounded_list(d['selections'],100,'/selections')):
                path='/selections/'+str(ix)
                try:
                    selection_object(row,{'target_refs','bindings','dependencies','replaces','residuals','reason'},{'target_refs','bindings','dependencies','replaces','residuals','reason'},path)
                    if contract_proposal_fingerprint(row) in s.get('contract_rejections',[]):raise ValueError('same_rejected_contract_proposal_no_change')
                    target_refs=bounded_list(row['target_refs'],40,path+'/target_refs')
                    binding_rows=bounded_list(row['bindings'],80,path+'/bindings')
                    dependency_rows=bounded_list(row['dependencies'],80,path+'/dependencies')
                    replacements=bounded_list(row['replaces'],40,path+'/replaces')
                    residual_rows=bounded_list(row['residuals'],100,path+'/residuals')
                    targets=[task_by_ref(s,x) for x in target_refs]
                    if not targets or any(x['target_ref'] not in call['target_refs'] for x in targets):raise ValueError('selection_current_targets_only')
                    if not isinstance(row['reason'],str) or not row['reason'].strip():raise ContractError('selection_reason_required',path+'/reason','nonempty string',row['reason'])
                    bindings=[];allowed_c={r for t in targets for r in t['candidate_refs']}
                    for j,b in enumerate(binding_rows):
                        bpath=path+'/bindings/'+str(j)
                        selection_object(b,{'candidate_ref','role','relation','relation_origin','relationship_ref','covers'},{'candidate_ref','role','relation','relation_origin','relationship_ref','covers'},bpath)
                        if b['candidate_ref'] not in allowed_c:raise ValueError('candidate_not_in_actual_target_pool')
                        if b['role'] not in ('core','site','unit','value','qualifier','document') or b['relation'] not in ('equivalent','label_association','target_broader','target_narrower','overlap','unresolved'):raise ValueError('selection_role_or_relation')
                        covers=bounded_list(b['covers'],40,bpath+'/covers')
                        if not covers or any(x not in row['target_refs'] for x in covers):raise ValueError('selection_explicit_coverage_targets')
                        if not any(b['candidate_ref'] in t['candidate_refs'] for t in targets if t['target_ref'] in b['covers']):raise ValueError('candidate_requires_actual_covered_target_receipt')
                        if b['relation_origin']=='vocabulary_relationship':
                            rel=s['relationships'].get(b['relationship_ref'])
                            if rel is None or rel['target_id']!=s['candidate_refs'][b['candidate_ref']] or not any(rel['source_id'] in {s['candidate_refs'][ref] for ref in t['candidate_refs']} for t in targets if t['target_ref'] in covers):raise ValueError('official_relationship_actual_direction_required')
                        elif b['relation_origin']!='model_semantic_judgment' or b['relationship_ref'] is not None:raise ValueError('semantic_relation_origin_required')
                        bindings.append(copy.deepcopy(b))
                    if not bindings:raise ValueError('selection_requires_actual_binding')
                    deps={t['unit_id']:t['revision'] for t in targets}
                    for j,dep in enumerate(dependency_rows):
                        selection_object(dep,{'target_ref','revision'},{'target_ref','revision'},path+'/dependencies/'+str(j))
                        if dep['target_ref'] not in units_by_id(s) or dep['revision']!=s['revisions'][dep['target_ref']]:raise ValueError('explicit_dependency_current_revision')
                        deps[dep['target_ref']]=dep['revision']
                    for oldref in replacements:
                        old,oldb=binding_owner(s,oldref)
                        if old is None:old=next((m for m in s['mappings'] if m['mapping_ref']==oldref),None)
                        if old is None:raise ValueError('replacement_unknown_mapping_or_binding')
                        oldcovers=oldb['covers'] if oldb else old['target_refs']
                        if not set(oldcovers)<=set(row['target_refs'])|set(x['target_ref'] for x in residual_rows):raise ValueError('replacement_must_preserve_old_coverage_or_residual')
                        if oldb is None:
                            identity=lambda b:(b['candidate_ref'],b['role'],b['relation'],b['relation_origin'],b['relationship_ref'],tuple(sorted(b['covers'])))
                            for retained in old['bindings']:
                                check=s['mapping_checks'].get(retained.get('binding_ref'),{})
                                affected=any(i['issue_ref'] in call.get('issue_refs',[]) and (i['target_ref']==retained.get('binding_ref') or i['required_action']=='patch_fact' and i['target_ref'] in {t['unit_id'] for t in s['tasks'] if t['target_ref'] in retained['covers']}) for i in s['issues'])
                                if retained.get('status') in ACCEPTED_BINDING_STATUSES and check.get('revision')==retained.get('revision') and check.get('status')=='no_issue_found' and not affected and not any(identity(b)==identity(retained) for b in bindings):raise ValueError('whole_replacement_must_retain_current_accepted_binding_not_residual')
                    for j,residual in enumerate(residual_rows):
                        rpath=path+'/residuals/'+str(j)
                        selection_object(residual,{'target_ref','reason','evidence_refs'},{'target_ref','reason','evidence_refs'},rpath)
                        if residual['target_ref'] not in row['target_refs']:raise ValueError('residual_requires_current_target')
                        bounded_list(residual['evidence_refs'],30,rpath+'/evidence_refs')
                        evidence(s,residual['evidence_refs'])
                    replaced_parents={binding_owner(s,ref)[0]['mapping_ref'] for ref in replacements if binding_owner(s,ref)[0] is not None}
                    if any(set(b['covers'])&set(row['target_refs']) and b.get('status') not in ('invalidated','unresolved') and b.get('binding_ref') not in replacements and m['mapping_ref'] not in replacements and m['mapping_ref'] not in replaced_parents for m in s['mappings'] for b in m['bindings']):raise ValueError('overlap_requires_explicit_replacement')
                    identity=lambda b:(b['candidate_ref'],b['role'],b['relation'],tuple(sorted(b['covers'])))
                    if any(identity(b)==identity(oldb) and oldb.get('status') not in ('invalidated','unresolved') and oldb.get('binding_ref') not in replacements and oldm['mapping_ref'] not in replacements for b in bindings for oldm in s['mappings'] for oldb in oldm['bindings']):raise ValueError('duplicate_existing_accepted_binding')
                    if set(row['target_refs'])-({r for b in bindings for r in b['covers']}|{x['target_ref'] for x in residual_rows}):raise ValueError('selection_target_not_covered_or_residual')
                    feedback_issues=[i for i in s['issues'] if i['issue_ref'] in call.get('issue_refs',[]) and i['required_action'] in ('retrieve','replace_mapping','revise_rationale','calibrate_relation') and set((binding_owner(s,i['target_ref'])[1] or {}).get('covers',[]))&set(row['target_refs'])]
                    if any(i['target_ref'] not in replacements and (binding_owner(s,i['target_ref'])[0] or {}).get('mapping_ref') not in replacements for i in feedback_issues):raise ValueError('retrieval_reselection_requires_explicit_replacement')
                    for feedback in feedback_issues:
                        if not feedback.get('calibration_only'):continue
                        _,prior=binding_owner(s,feedback['target_ref'])
                        same=[b for b in bindings if b['candidate_ref']==prior['candidate_ref'] and b['role']==prior['role'] and set(b['covers'])==set(prior['covers'])]
                        fresh=set(meaning_refs(s,prior['candidate_ref']))!=set(feedback.get('meaning_evidence_refs',[]))
                        if same and any(b['relation']==prior['relation'] for b in same) and not fresh:raise ValueError('same_unproven_relation_requires_calibration_or_actual_new_evidence')
                    rationale_only=[]
                    semantic_identity=lambda b:(b['candidate_ref'],b['role'],b['relation'],b['relation_origin'],b['relationship_ref'],tuple(sorted(b['covers'])))
                    for issue in feedback_issues:
                        if issue['kind'] not in RATIONALE_KINDS and not issue.get('evidence_gate_only'):continue
                        old,oldb=binding_owner(s,issue['target_ref'])
                        if issue['expected_revision']!=oldb['revision'] or not all(target_current(s,task_by_ref(s,r)) for r in oldb['covers']):raise ValueError('rationale_issue_current_binding_revision_required')
                        conflict=any(i['target_ref']==issue['target_ref'] and i['expected_revision']==oldb['revision'] and i['kind'] in MAPPING_KINDS and not i.get('evidence_gate_only') for i in s['issues'])
                        if row['reason'].strip()==old['reason'].strip():raise ValueError('rationale_revision_requires_changed_reason')
                        if issue.get('rationale_mapping_ref'):
                            if old['mapping_ref'] not in replacements:raise ValueError('shared_rationale_requires_whole_mapping_replacement')
                            if not set(old['target_refs'])<=set(row['target_refs']):raise ValueError('shared_rationale_requires_all_original_targets')
                            for prior in old['bindings']:
                                semantic_conflict=any(i['target_ref']==prior['binding_ref'] and i['expected_revision']==prior['revision'] and i['kind'] in MAPPING_KINDS and not i.get('evidence_gate_only') for i in s['issues'])
                                if not semantic_conflict and not any(semantic_identity(b)==semantic_identity(prior) for b in bindings):raise ValueError('shared_rationale_replacement_must_retain_uncontested_binding_semantics')
                        if conflict:continue
                        if not any(semantic_identity(b)==semantic_identity(oldb) for b in bindings):raise ValueError('rationale_revision_requires_same_binding_semantics')
                        rationale_only.append(issue['issue_ref'])
                    target_evidence=[{'query_id':q['query_id'],'receipt_ref':q['receipt_ref'],'term':q['term'],'candidate_refs':q['candidate_refs'],'actual_stats':q['actual_stats']} for q in s['queries'] if q['target_ref'] in row['target_refs']]
                    fingerprint=sha({'targets':[(t['target_ref'],t['revision']) for t in targets],'bindings':bindings,'dependencies':deps,'actual_target_evidence':target_evidence,'actual_meaning_evidence':{b['candidate_ref']:meaning_refs(s,b['candidate_ref']) for b in bindings},'source_sha256':s['source']['source_sha256']})
                    if fingerprint in s['rejected_fingerprints']:raise ValueError('same_rejected_selection_no_new_evidence')
                    m=dict(copy.deepcopy(row),proposed_reason=row['reason'],proposed_reason_status='not_independently_reviewed_not_published',mapping_ref='m'+str(len(s['mappings'])+1),revision=1,dependencies=[{'target_ref':r,'revision':v} for r,v in deps.items()],status='pending_review',fingerprint=fingerprint,semantic_correctness_verified=False)
                    if feedback_issues:m['feedback_issue_refs']=[i['issue_ref'] for i in feedback_issues]
                    for ix,b in enumerate(m['bindings'],1):
                        b.update(binding_ref=m['mapping_ref']+'b'+str(ix),revision=1,status='pending_review',proposed_relation=b['relation'],accepted_evidence_strength='not_checked',semantic_correctness_verified=False)
                        b['fingerprint']=sha({'targets':[(t['target_ref'],t['revision']) for t in targets if t['target_ref'] in b['covers']],'binding':{k:v for k,v in b.items() if k not in ('binding_ref','revision','status','semantic_correctness_verified')},'actual_meaning_evidence':meaning_refs(s,b['candidate_ref']),'source_sha256':s['source']['source_sha256'],'actual_target_evidence':[q for q in target_evidence if any(t['target_ref'] in b['covers'] and q['query_id'] in [v['query_id'] for v in s['queries'] if v['target_ref']==t['target_ref']] for t in targets)]})
                        if b['fingerprint'] in s['rejected_fingerprints']:raise ValueError('same_rejected_binding_no_new_evidence')
                        if sha({'binding_fingerprint':b['fingerprint'],'reason':row['reason'].strip()}) in s.get('rejected_rationales',{}):raise ValueError('same_rejected_rationale_no_change')
                    prior_bindings=[b for old in s['mappings'] for b in old['bindings'] if old['mapping_ref'] in replacements or b.get('binding_ref') in replacements]
                    same_evidence=prior_bindings and all(b.get('fingerprint') for b in prior_bindings) and sorted(b['fingerprint'] for b in prior_bindings)==sorted(b['fingerprint'] for b in m['bindings'])
                    actual_retrieval=any(i['required_action']=='retrieve' and i.get('retrieval_receipts') and i['expected_revision']==binding_owner(s,i['target_ref'])[1]['revision'] for i in feedback_issues)
                    if same_evidence and not rationale_only and not actual_retrieval:raise ValueError('unchanged_binding_replacement_requires_current_feedback_or_new_evidence')
                    for old in s['mappings']:
                        for oldb in old['bindings']:
                            if old['mapping_ref'] in row['replaces'] or oldb.get('binding_ref') in row['replaces']:oldb['status']='invalidated'
                        refresh_mapping(s,old)
                    s['mappings'].append(m);new.append(m['mapping_ref']);accepted.append(m['mapping_ref'])
                    for t in targets:
                        t['status']='selected'
                        outcomes[t['target_ref']]={'status':'explicit_residual' if any(r['target_ref']==t['target_ref'] for r in residual_rows) else 'accepted_pending_review',
                            'written_binding_refs':[b['binding_ref'] for b in m['bindings'] if t['target_ref'] in b['covers']],'mapping_ref':m['mapping_ref'],'semantic_repair_verified':False}
                    for i in feedback_issues:
                        i['status']='reselected_pending_review';i.setdefault('replacement_mapping_refs',[]).append(m['mapping_ref']);i.setdefault('replacement_binding_refs',[]).extend(b['binding_ref'] for b in m['bindings'] if i.get('rationale_mapping_ref') or set(b['covers'])&set((binding_owner(s,i['target_ref'])[1] or {}).get('covers',[])))
                        if i['issue_ref'] in rationale_only:
                            if i.get('evidence_gate_only'):i['evidence_contract_correction_pending_offline']=True
                            else:i['rationale_only_no_concept_change']=True
                except (ValueError,TypeError,KeyError,IndexError) as e:
                    rejected.append(plan_rejection(e,row,selection_targets_for_feedback(row,call)))
                    if isinstance(e,ContractError):s.setdefault('contract_rejections',[]).append(contract_proposal_fingerprint(row))
            for ix,row in enumerate(bounded_list(d['unresolved'],100,'/unresolved')):
                try:
                    selection_object(row,{'target_ref','reason'},{'target_ref','reason'},'/unresolved/'+str(ix))
                    if not isinstance(row['reason'],str) or not 1<=len(row['reason'].strip())<=600:raise ContractError('selection_short_reason_required','/unresolved/'+str(ix)+'/reason','nonempty string; max 600 characters',row['reason'])
                    if row['target_ref'] not in call['target_refs']:raise ValueError('selection_unresolved_target')
                    t=task_by_ref(s,row['target_ref'])
                    if t['status']=='selected':raise ValueError('selected_target_cannot_also_be_unresolved')
                    t['status']='unresolved';t['unresolved_reason']=row['reason']
                    outcomes[row['target_ref']]={'status':'unresolved_explicit','reason':row['reason'],'semantic_repair_verified':False}
                except (ValueError,TypeError,KeyError,IndexError) as e:
                    r=[row['target_ref']] if isinstance(row,dict) and row.get('target_ref') in call['target_refs'] else call['target_refs']
                    rejected.append(plan_rejection(e,row,r))
            for ref in call['target_refs']:
                t=task_by_ref(s,ref)
                if ref not in outcomes and t['status']!='awaiting_meaning' and not any(ref in r.get('target_refs',[]) for r in rejected):
                    rejected.append(plan_rejection(ContractError('selection_target_not_decided','/selections|/unresolved','a selection or explicit unresolved for '+ref,None),target_refs=[ref]))
            settle_selection_targets(s,call,rejected,outcomes)
            if new:set_review(s,'mapping_delta' if call.get('issue_refs') else 'mapping_verify',new)
            else:s['call']=None
        else:raise ValueError('planner_mode_owned_by_code')
        log(s,mode,accepted,rejected,info)
    except (ValueError,TypeError,KeyError,IndexError) as e:
        rejected.append(plan_rejection(e,target_refs=call.get('target_refs',[])))
        log(s,mode or 'planner',accepted,rejected);s['call']=None
        for ref in call.get('target_refs',[]):
            t=task_by_ref(s,ref)
            if mode=='query_only' and not t.get('expansion_attempted'):t['expansion_feedback']={'code':'query_expansion_not_executed','target_ref':ref,'reason':str(e),'semantic_repair_claimed':False}
            elif mode=='select_only' and t['status']!='selected':
                if isinstance(e,ContractError):continue
                t['status']='unresolved';t['unresolved_reason']='selection_contract_failed'
        if mode=='select_only':
            settle_selection_targets(s,call,rejected,outcomes)
            if accepted:set_review(s,'mapping_delta' if call.get('issue_refs') else 'mapping_verify',accepted)
        elif mode=='query_only':
            for i in s['issues']:
                if i['issue_ref'] in call.get('issue_refs',[]) and i['required_action']=='retrieve':i['status']='unresolved';i['unresolved_reason']='query_not_reserved:'+str(e)
    return dispatch_output(s)


def eligible(c,date):
    required=set(CONCEPT_FIELDS)|{'vocabulary_version'}
    if not required<=set(c) or any(c[k] is None for k in required-{'invalid_reason','standard_concept'}):return 'pending_metadata'
    if c['standard_concept'] not in ('S','C',None) or c['invalid_reason'] not in ('D','U',None):return 'pending_metadata'
    try:
        start=datetime.date.fromisoformat(str(c['valid_start_date']));end=datetime.date.fromisoformat(str(c['valid_end_date']));at=datetime.date.fromisoformat(date)
        if start>end:return 'pending_metadata'
        return 'eligible_standard' if c['standard_concept']=='S' and c['invalid_reason'] is None and start<=at<=end else 'not_eligible'
    except (ValueError,TypeError):return 'pending_metadata'


def apply_search(state_json,batch_receipt_json):
    s=state_load(state_json);p=s['pending_query']
    if p and p.get('mode')=='meaning':return apply_meaning_receipt(s,obj(batch_receipt_json))
    try:
        r=obj(batch_receipt_json)
        if r.get('mode','search')!='search' or r.get('input_request',p.get('request') if p else None)!=(p.get('request') if p else None):raise ValueError('search_receipt_pending_request_mismatch')
        if not p or r.get('batch_id')!=p['batch_id'] or r.get('terms')!=p['terms']:raise ValueError('receipt_pending_identity_mismatch')
        if r.get('safety',{}).get('transaction_read_only')!='on':raise ValueError('readonly_not_verified')
        if r.get('error'):raise ValueError('query_failed:'+str(r['error']))
        date=datetime.date.fromisoformat(r['verification_date']).isoformat();versions={v['vocabulary_id']:v.get('vocabulary_version') for v in r.get('vocabulary_versions',[])}
        access={};allids={c['concept_id'] for c in r['concepts']}
        for e in r['retrieval_evidence']:
            if e['term'] not in p['terms'] or e['concept_id'] not in allids:raise ValueError('receipt_retrieval_path_mismatch')
            access.setdefault(e['concept_id'],set()).add(e['term'])
        for i,rel in enumerate(r['relationships']):
            if rel['source_id'] not in access or rel['target_id'] not in allids:raise ValueError('relationship_actual_path_required')
            access.setdefault(rel['target_id'],set()).update(access[rel['source_id']]);s['relationships'][p['batch_id']+'r'+str(i+1)]=dict(rel,receipt_ref=p['batch_id'],vocabulary_versions=versions,verification_date=date)
        for raw in r['concepts']:
            if raw['concept_id'] not in access:raise ValueError('candidate_without_actual_retrieval_path')
            c=copy.deepcopy(raw);c['vocabulary_version']=versions.get(c.get('vocabulary_id'));c['eligibility']=eligible(c,date);c['verified_at']=date
            cid=str(c['concept_id']);old=s['catalog'].get(cid)
            if old and any(old.get(k)!=c.get(k) for k in CONCEPT_FIELDS):raise ValueError('conflicting_actual_identity')
            if old and old.get('vocabulary_version')==c.get('vocabulary_version'):c['meaning_evidence_refs']=copy.deepcopy(old.get('meaning_evidence_refs',[]))
            s['catalog'][cid]=c
            if c['concept_id'] not in s['candidate_refs'].values():s['candidate_refs']['c'+str(len(s['candidate_refs'])+1)]=c['concept_id']
        meaning_retained,meaning_rejected=retain_meaning_evidence(s,r.get('meaning_evidence',[]),date,allids,r)
        if meaning_rejected:log(s,'meaning_receipt_contract',rejected=meaning_rejected,info={'lexical_neighbors_preserved':True,'semantic_repair_claimed':False})
        stats={x['term']:x for x in r['term_results']}
        if set(stats)!=set(p['terms']):raise ValueError('receipt_term_statistics_mismatch')
        for q in p['tasks']:
            t=task_by_ref(s,q['target_ref']);cids=[ref for ref,cid in s['candidate_refs'].items() if q['term'] in access.get(cid,set())]
            t['candidate_refs']=sorted(set(t['candidate_refs'])|set(cids));t['query_status']='executed_within_declared_plan'
            s['queries'].append(dict(q,receipt_ref=p['batch_id'],candidate_refs=cids,actual_stats=copy.deepcopy(stats[q['term']]),exhaustive=False,zero_hit_is_not_absence=True))
        for i in s['issues']:
            if i['issue_ref'] not in p.get('issue_refs',[]) or i['required_action']!='retrieve':continue
            old,oldb=binding_owner(s,i['target_ref'])
            oldcovers=oldb['covers'] if oldb else []
            relevant=[q for q in p['tasks'] if q['target_ref'] in oldcovers]
            if not relevant:continue
            reopened=[task_by_ref(s,ref) for ref in oldcovers]
            for t in reopened:t['status']='not_selected';t.pop('unresolved_reason',None)
            i['status']='retrieved_pending_reselection';i['reselection_target_refs']=[t['target_ref'] for t in reopened]
            i.setdefault('retrieval_receipts',[]).append({'receipt_ref':p['batch_id'],'query_ids':[q['query_id'] for q in relevant]})
        log(s,'retrieve',info={'batch_id':p['batch_id'],'actual_candidate_count':len(r['concepts']),'exhaustive':False})
    except (ValueError,TypeError,KeyError,IndexError) as e:
        log(s,'retrieve',rejected=[{'code':str(e)}])
        for q in p['tasks'] if p else []:
            t=next((x for x in s['tasks'] if x['target_ref']==q['target_ref']),None)
            if t:t['query_status']='incomplete';t['query_error']=str(e)
        for i in s['issues']:
            if i['issue_ref'] in (p or {}).get('issue_refs',[]) and i['required_action']=='retrieve':i['status']='unresolved';i['unresolved_reason']='retrieval_failed:'+str(e)
    s['pending_query']=None;s['call']=None;return output(s)


def close_superseded_auxiliary_issues(s,seen,review_ref):
    """Close only evidenced same-semantics replacement paths after a current reason review."""
    owners={b['binding_ref']:(m,b) for m in s['mappings'] for b in m['bindings']}
    def signature(m,b):
        return (s['source']['source_sha256'],s['candidate_refs'].get(b['candidate_ref']),b['role'],b['relation'],b['relation_origin'],b.get('relationship_ref'),tuple(sorted(b['covers'])),tuple(sorted((d['target_ref'],d['revision']) for d in m['dependencies'])))
    edges={ref:[] for ref in owners}
    for ref,(m,b) in owners.items():
        for oldref,(oldm,oldb) in owners.items():
            if oldref!=ref and (oldref in m['replaces'] or oldm['mapping_ref'] in m['replaces']) and signature(m,b)==signature(oldm,oldb):edges[oldref].append(ref)
    for issue in s['issues']:
        if issue['kind'] not in RATIONALE_KINDS or issue['status']!='reselected_pending_review':continue
        start=issue['target_ref'];paths=[];stack=[(start,[start])]
        while stack:
            ref,path=stack.pop()
            if ref not in owners:continue
            m,b=owners[ref];check=s['mapping_checks'].get(ref,{})
            current=all(s['revisions'].get(d['target_ref'])==d['revision'] for d in m['dependencies']) and all(target_current(s,task_by_ref(s,t)) for t in b['covers'])
            valid=ref in seen and ref!=start and current and b.get('status') in ACCEPTED_BINDING_STATUSES and check.get('status')=='no_issue_found' and check.get('structured_review_ref')==review_ref and m.get('reason_status')=='supported_pending_offline' and m.get('reason_review',{}).get('review_ref')==review_ref
            if valid:paths.append(path)
            for following in edges.get(ref,[]):
                if following not in path:stack.append((following,path+[following]))
        if paths:
            issue['status']='model_checked_repair_pending_offline';issue['offline_semantic_repair_verified']=False
            issue['auxiliary_closure_trace']={'replacement_paths':paths,'review_ref':review_ref,'basis':'actual_replaces_same_semantics_current_binding_and_reason_review'}


def mapping_apply_review(s,review_json):
    call=copy.deepcopy(s.get('call') or {});mode=call.get('mode');accepted=[];rejected=[];seen=set();issue_targets=set();query_targets=set();nonquery_targets=set();rationale_maps=set()
    s['explanation_contract']='typed_assertions_e8'
    requested={x['target_ref']:x['revision'] for x in call['targets']};prior_checks=copy.deepcopy(s['mapping_checks']);prior_status={ref:(binding_owner(s,ref)[1] or {}).get('status') for ref in requested};contract_bindings=set();contract_maps=set();current_view=view_for_call(s);review_ref=None
    def source_for_binding(b):
        return binding_source_evidence(s,b)
    def register_issue(raw,structured=None):
        try:
            if raw.get('kind') not in QUERY_KINDS|MAPPING_KINDS|RATIONALE_KINDS|{'scope_or_context'}:raise ValueError('mapping_review_cannot_reextract')
            if raw.get('kind')=='meaning_evidence_missing':
                _,actual_b=binding_owner(s,raw.get('target_ref'))
                if actual_b and meaning_refs(s,actual_b['candidate_ref']):raise ValueError('meaning_evidence_already_available_use_actual_refs_or_specific_semantic_gap')
            dependencies={dep['target_ref'] for ref in requested for dep in (binding_owner(s,ref)[0] or {}).get('dependencies',[])}
            allowed=dependencies if raw.get('kind')=='scope_or_context' else requested
            if raw.get('target_ref') in requested:
                _,source_b=binding_owner(s,raw['target_ref']);source_lines={sp['line_no'] for t in s['tasks'] if t['target_ref'] in source_b['covers'] for sp in units_by_id(s)[t['unit_id']]['spans']}
                if any(x['line_no'] not in source_lines for x in evidence(s,raw['evidence_refs'])):raise ValueError('issue_evidence_outside_binding_source_scope')
            auxiliary=raw['kind'] in RATIONALE_KINDS
            gap_key=None;issue=None
            if auxiliary:
                parent,_=binding_owner(s,raw['target_ref'])
                gap_key=sha({'kind':'auxiliary_reason','source':s['source']['source_sha256'],'semantics':[(s['candidate_refs'][b['candidate_ref']],b['role'],b['relation'],sorted((task_by_ref(s,r)['unit_id'],task_by_ref(s,r)['field_path'],task_by_ref(s,r)['value_sha256']) for r in b['covers'])) for b in parent['bindings']]})
                issue=next((old for old in s['issues'] if old.get('auxiliary_gap_key')==gap_key),None)
                if issue:
                    if issue['target_ref']!=raw['target_ref']:issue.setdefault('prior_targets',[]).append({'target_ref':issue['target_ref'],'revision':issue['expected_revision']})
                    issue.update(target_ref=raw['target_ref'],expected_revision=refs(s)[raw['target_ref']]['revision'],detail=raw['detail'],evidence_refs=evidence(s,raw['evidence_refs']),status='open')
            final_action='calibrate_relation' if (structured or {}).get('calibration_only') else 'revise_rationale' if (structured or {}).get('evidence_gate_only') else None
            issue=issue or issue_for(s,raw,allowed,gate=issue_gate(raw,structured),required_action=final_action)
            if gap_key:issue['auxiliary_gap_key']=gap_key
            ref=issue['target_ref'];accepted.append(issue['issue_ref'])
            auxiliary=issue['kind'] in RATIONALE_KINDS
            calibration=(structured or {}).get('calibration_only') is True
            if not auxiliary and not calibration:issue_targets.add(ref)
            if (issue.get('evidence_gate_only') or issue.get('calibration_only')) and not (structured or {}).get('evidence_gate_only') and not calibration:
                issue.setdefault('prior_evidence_contract_feedback',[]).append(copy.deepcopy(issue.get('structured_feedback',{})))
                issue.pop('evidence_gate_only',None);issue.pop('calibration_only',None);issue.pop('subcode',None);issue['required_action']='replace_mapping'
                if not structured:issue['origin']='model_issue';issue['actual_semantic_issue']=copy.deepcopy(raw)
            if structured:
                issue['origin']='code_structured_review_gate';issue['structured_feedback']=copy.deepcopy(structured)
                issue.setdefault('gate_observations',[]).append({key:copy.deepcopy(structured.get(key)) for key in ('gate','review_ref','calibration_only','strict_direction_negative')})
                if structured.get('evidence_gate_only'):issue['evidence_gate_only']=True;issue['subcode']='strict_direction_lexical_evidence_contract_not_met';issue['required_action']='revise_rationale'
            if calibration:
                if issue.get('actual_semantic_issue') or ref in nonquery_targets:return
                issue.update(calibration_only=True,required_action='calibrate_relation',strict_direction_negative=bool(structured.get('strict_direction_negative')) or issue.get('strict_direction_negative',False),meaning_evidence_refs=meaning_refs(s,binding_owner(s,ref)[1]['candidate_ref']))
                return
            if issue['kind'] in RATIONALE_KINDS:
                parent,_=binding_owner(s,ref);issue['rationale_mapping_ref']=parent['mapping_ref'];issue['required_action']='recheck_rationale';issue['auxiliary_only']=True
                issue['evidence_scope_sha256']=rationale_scope_digest(view_for_call(s),parent['mapping_ref'])
                return
            (query_targets if issue['kind'] in QUERY_KINDS else nonquery_targets).add(ref)
            if ref in requested:
                m,b=binding_owner(s,ref)
                if issue['kind'] in QUERY_KINDS:b['status']='retrieval_incomplete'
                else:binding_invalidate(s,ref,issue['issue_ref'])
                refresh_mapping(s,m)
            else:invalidate(s,[ref])
        except (ValueError,TypeError,KeyError,IndexError) as error:rejected.append({'code':str(error),'proposal':raw})
    try:
        d=parse(s,review_json,mode)
        checks,assertions,obligations,registry,contract_rejections,incomplete_maps,raw_negatives=validate_structured_mapping_review(s,call,d);rejected.extend(contract_rejections)
        review_ref='review'+sha({'call':call,'reply':d})[:20];current_view=view_for_call(s)
        s.setdefault('structured_mapping_reviews',[]).append({'review_ref':review_ref,'mode':mode,'targets':copy.deepcopy(call['targets']),'assertion_obligations':list(copy.deepcopy(obligations).values()),'actual_reply':copy.deepcopy(d),'contract_rejections':copy.deepcopy(contract_rejections),'semantic_correctness_verified':False})
        contract_bindings.update(set(requested)-set(checks));contract_maps.update(incomplete_maps)
        by_binding={ref:{ob['predicate']:assertions.get(aid) for aid,ob in obligations.items() if ob['binding_ref']==ref} for ref in requested}
        for negative in raw_negatives:
            ref=negative['binding_refs'][0];_,binding=binding_owner(s,ref)
            source_negative=(negative.get('binding_check') or {}).get('assessment',{}).get('association') in ('unsupported','not_checkable') or (negative.get('obligation') or {}).get('predicate')=='source_association'
            register_issue({'target_ref':ref,'kind':'unsupported_specificity','evidence_refs':source_for_binding(binding),'detail':'Verified current identity and source carry an explicit negative '+('association' if source_negative else 'equivalence direction')+' model verdict.'},{'review_ref':review_ref,'raw_negative':negative,'calibration_only':not source_negative,'strict_direction_negative':not source_negative,'gate':'validated_raw_single_predicate_negative'})
        for ref,check in checks.items():
            mapping,binding=binding_owner(s,ref);assessment=check['assessment'];typed=by_binding[ref];association=typed.get('source_association');eq=typed.get('source_equivalence')
            association_ok=assessment['association']=='supported' and association is not None and association['verdict']=='supported'
            if assessment['association']!='supported' or association is not None and association['verdict']!='supported':
                register_issue({'target_ref':ref,'kind':'unsupported_specificity','evidence_refs':check['source_evidence'],'detail':'Current single-predicate source_association is not supported; this is a model assessment, not independent wrong-ID confirmation.'},{'review_ref':review_ref,'binding_check':check,'assertion_check':association,'gate':'source_association_not_supported'})
            if association is None:contract_bindings.add(ref)
            binding['proposed_relation']=binding['relation'];binding['model_eq_verdict']={'forward':assessment['forward'],'reverse':assessment['reverse'],'source_equivalence':eq['verdict'] if eq else 'not_checked'};binding['strict_semantic_equivalence_verified']=False
            names=[s['catalog'][str(s['candidate_refs'][binding['candidate_ref']])].get('concept_name')]+[row['term'] for mr in meaning_refs(s,binding['candidate_ref']) for row in s['meaning_evidence'][mr]['synonyms']]
            lexical=bool(binding['covers']) and all(isinstance(task_by_ref(s,target)['value'],str) and task_by_ref(s,target)['value'].strip().casefold() in {name.strip().casefold() for name in names if isinstance(name,str)} for target in binding['covers'])
            partial=any(residual['target_ref'] in binding['covers'] for residual in mapping['residuals'])
            binding['accepted_evidence_strength']='partial_label_association' if association_ok and partial else 'lexical_identity' if association_ok and lexical else 'model_checked_label_association' if association_ok else 'not_established'
            if binding['relation']=='equivalent' and association_ok and eq is not None:
                strict_negative=any(assessment[key]!='supported' for key in ('forward','reverse')) or eq['verdict']!='supported'
                register_issue({'target_ref':ref,'kind':'unsupported_specificity','evidence_refs':check['source_evidence'],'detail':'Requested strict equivalence has no verified semantic definition/role-group proof in the current vocabulary schema; preserve the supported label and calibrate the proposed relation or obtain actual additional evidence.'},{'review_ref':review_ref,'calibration_only':True,'strict_direction_negative':strict_negative,'model_eq_verdict':copy.deepcopy(binding['model_eq_verdict']),'accepted_evidence_strength':binding['accepted_evidence_strength'],'gate':'strict_equivalence_not_established_by_labels_or_model_votes'})
        for raw in d['issues']:
            try:
                only(raw,{'target_ref','kind','evidence_refs','detail'},{'target_ref','kind','evidence_refs','detail'})
                target=raw.get('target_ref');parent,binding=binding_owner(s,target)
                scopes=[parent['mapping_ref']] if parent else [m['mapping_ref'] for m in s['mappings'] if any(dep['target_ref']==target for dep in m['dependencies'])]
                normalized=copy.deepcopy(raw);normalized['evidence_refs']=source_evidence_from_refs(s,registry,raw.get('evidence_refs'),scopes)
                register_issue(normalized)
            except (ValueError,TypeError,KeyError,IndexError) as error:rejected.append({'code':str(error),'proposal':copy.deepcopy(raw),'contract_only':True})
        for ref,check in checks.items():
            mapping,binding=binding_owner(s,ref);concept=s['catalog'][str(s['candidate_refs'][binding['candidate_ref']])];association=by_binding[ref].get('source_association')
            challenged=ref in issue_targets or any(dep['target_ref'] in issue_targets for dep in mapping['dependencies']);complete=ref not in contract_bindings and association is not None
            status='issue' if challenged else 'no_issue_found' if complete else 'unresolved'
            derived=dict(copy.deepcopy(check),status=status,mode=mode,model_checked_current_revision=complete,contract_complete=complete,structured_review_ref=review_ref,assertion_checks=copy.deepcopy(by_binding[ref]),reason_hash=hashlib.sha256(mapping['reason'].encode()).hexdigest(),semantic_correctness_verified=False)
            if not complete and not challenged and prior_status.get(ref) in ACCEPTED_BINDING_STATUSES and prior_checks.get(ref,{}).get('revision')==binding['revision'] and prior_checks[ref].get('status')=='no_issue_found':s['mapping_checks'][ref]=prior_checks[ref];binding['status']=prior_status[ref];seen.add(ref);continue
            s['mapping_checks'][ref]=derived
            if ref in nonquery_targets or binding.get('status')=='invalidated':binding['status']='invalidated'
            elif ref in query_targets:binding['status']='retrieval_incomplete'
            elif not complete:binding['status']='pending_review'
            elif not challenged and association['verdict']=='supported':binding['status']='proposed_partial' if binding['accepted_evidence_strength']=='partial_label_association' or concept['eligibility']!='eligible_standard' else 'proposed_label_association'
            else:binding['status']='unresolved'
            mapping['proposed_reason']=mapping['reason'];mapping['proposed_reason_status']='not_independently_reviewed_not_published';mapping['reason_status']='raw_proposal_only'
            refresh_mapping(s,mapping);seen.add(ref);accepted.append(ref)
        for ref in set(requested)-set(checks):
            mapping,binding=binding_owner(s,ref)
            if binding is not None and binding.get('status')!='invalidated':
                if prior_status.get(ref) in ACCEPTED_BINDING_STATUSES and prior_checks.get(ref,{}).get('revision')==binding['revision'] and prior_checks[ref].get('status')=='no_issue_found':binding['status']=prior_status[ref];s['mapping_checks'][ref]=prior_checks[ref]
                else:binding['status']='pending_review';s['mapping_checks'].pop(ref,None)
                refresh_mapping(s,mapping)
        for issue in s['issues']:
            if issue['status']!='reselected_pending_review':continue
            replacements=issue.get('replacement_binding_refs',[])
            reviewed=bool(replacements) and all(ref in seen and s['mapping_checks'].get(ref,{}).get('status')=='no_issue_found' and binding_owner(s,ref)[1].get('status') in ACCEPTED_BINDING_STATUSES|{'proposed_partial'} for ref in replacements)
            actual_queries={q['query_id']:q for q in s['queries']}
            receipt=issue['required_action']!='retrieve' or bool(issue.get('retrieval_receipts')) and all((row.get('query_ids') and all(qid in actual_queries and actual_queries[qid]['receipt_ref']==row['receipt_ref'] for qid in row['query_ids'])) or (row.get('meaning_request_refs') and all(any(request['request_ref']==rid and request['status']=='received' and request.get('receipt_ref')==row['receipt_ref'] and set(row.get('meaning_evidence_refs',[]))<=set(request.get('meaning_evidence_refs',[])) for request in s.get('meaning_requests',[])) for rid in row['meaning_request_refs'])) for row in issue['retrieval_receipts'])
            rationale_complete=issue['kind'] not in RATIONALE_KINDS or bool(replacements) and all((binding_owner(s,ref)[0] or {}).get('reason_status')=='supported_pending_offline' and (binding_owner(s,ref)[0] or {}).get('reason_review',{}).get('review_ref')==review_ref for ref in replacements)
            if reviewed and receipt and rationale_complete:issue['status']='model_checked_repair_pending_offline';issue['offline_semantic_repair_verified']=False
        # Historical free-reason issues remain in trace; their withdrawal is not a repair verdict.
        log(s,mode,accepted,rejected,{'requested_count':len(requested),'actually_checked_count':len(seen),'review_scope':'current_binding_single_predicate_assertions','structured_review_ref':review_ref,'incomplete_assertion_mappings':sorted(incomplete_maps),'semantic_correctness_verified':False})
    except (ValueError,TypeError,KeyError,IndexError) as error:log(s,mode or 'review',accepted,rejected+[{'code':str(error),'contract_only':True}])
    contract_maps.update((binding_owner(s,ref)[0] or {}).get('mapping_ref') for ref in contract_bindings)
    if review_ref is None:contract_maps.update((binding_owner(s,ref)[0] or {}).get('mapping_ref') for ref in requested);review_ref='contract'+sha({'call':call,'reply':review_json})[:20]
    if contract_maps or call.get('mapping_contract_retry_keys'):queue_mapping_contract_feedback(s,call,[row for row in rejected if row.get('contract_only')],contract_maps,current_view,review_ref)
    s['call']=None;return output(s)


def apply_review(state_json,review_json):
    s=state_load(state_json);call=copy.deepcopy(s.get('call') or {});mode=call.get('mode');accepted=[];rejected=[]
    if mode in ('mapping_verify','mapping_delta'):return mapping_apply_review(s,review_json)
    try:
        if mode not in ('source_compare','source_delta','mapping_verify','mapping_delta'):raise ValueError('review_mode_owned_by_code')
        d=parse(s,review_json,mode);only(d,{'checks','issues'},{'checks','issues'})
        requested={x['target_ref']:x['revision'] for x in call['targets']};known=refs(s);issue_targets=set();query_targets=set();nonquery_targets=set()
        def register_issue(raw,current_checks=None):
            try:
                if mode.startswith('source') and raw.get('kind') not in SOURCE_KINDS:raise ValueError('source_review_cannot_map_or_query')
                if mode.startswith('mapping') and raw.get('kind') not in QUERY_KINDS|MAPPING_KINDS|{'scope_or_context'}:raise ValueError('mapping_review_cannot_reextract')
                dependencies={dep['target_ref'] for m in s['mappings'] if m['mapping_ref'] in requested for dep in m['dependencies']}
                allowed=dependencies if mode.startswith('mapping') and raw.get('kind')=='scope_or_context' else requested
                issue=issue_for(s,raw,allowed,current_checks);issue_targets.add(issue['target_ref']);accepted.append(issue['issue_ref'])
                (query_targets if issue['kind'] in QUERY_KINDS else nonquery_targets).add(issue['target_ref'])
                if mode.startswith('mapping') and issue['target_ref'] in requested:
                    m=next(x for x in s['mappings'] if x['mapping_ref']==issue['target_ref'])
                    if issue['kind'] in QUERY_KINDS:m['status']='retrieval_incomplete'
                    else:m['status']='invalidated';s['rejected_fingerprints'][m['fingerprint']]=issue['issue_ref']
                elif mode.startswith('mapping'):
                    for m in s['mappings']:
                        if any(dep['target_ref']==issue['target_ref'] for dep in m['dependencies']):m['status']='invalidated'
            except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append({'code':str(e),'proposal':raw})
        if mode.startswith('mapping'):
            for raw in bounded_list(d['issues'],100):register_issue(raw)
        seen=set();submitted=set()
        for check in bounded_list(d['checks'],200):
            try:
                needed={'target_ref','revision','status','detail'}|({'fact_refs'} if mode.startswith('source') else set());only(check,needed,needed)
                ref=check['target_ref']
                if ref not in requested or ref in submitted or check['revision']!=requested[ref] or known[ref]['revision']!=requested[ref]:raise ValueError('check_actual_requested_revision_required')
                submitted.add(ref)
                if mode.startswith('source'):
                    if check['status'] not in ('represented','missing','ambiguous'):raise ValueError('source_check_status')
                    if any(r not in units_by_id(s) for r in bounded_list(check['fact_refs'],160)):raise ValueError('source_check_actual_fact_refs_required')
                    if check['status'] in ('represented','ambiguous') and not check['fact_refs']:raise ValueError('source_status_requires_actual_fact_attachment')
                    s['source_checks'][ref]=dict(copy.deepcopy(check),mode=mode,model_checked_current_revision=True,semantic_correctness_verified=False)
                else:
                    if check['status'] not in ('no_issue_found','issue','unresolved'):raise ValueError('mapping_check_status')
                    m=next(x for x in s['mappings'] if x['mapping_ref']==ref)
                    s['mapping_checks'][ref]=dict(copy.deepcopy(check),mode=mode,semantic_correctness_verified=False)
                    crows=[s['catalog'][str(s['candidate_refs'][b['candidate_ref']])] for b in m['bindings']]
                    precise=all(b['relation']=='equivalent' for b in m['bindings']) and all(c['eligibility']=='eligible_standard' for c in crows) and not m['residuals']
                    challenged=ref in issue_targets or any(dep['target_ref'] in issue_targets for dep in m['dependencies'])
                    only_query_challenged=ref in query_targets and ref not in nonquery_targets and not any(dep['target_ref'] in nonquery_targets for dep in m['dependencies'])
                    m['status']='retrieval_incomplete' if only_query_challenged else 'proposed_equivalent' if check['status']=='no_issue_found' and not challenged and precise else 'proposed_partial' if check['status']=='no_issue_found' and not challenged else 'unresolved'
                seen.add(ref)
                accepted.append(ref)
            except (ValueError,TypeError,KeyError,IndexError) as e:rejected.append({'code':str(e),'proposal':check})
        if mode.startswith('source'):
            for raw in bounded_list(d['issues'],100):register_issue(raw,seen)
        for ref in set(requested)-seen:rejected.append({'code':'required_target_not_checked','target_ref':ref,'revision':requested[ref]})
        for issue in s['issues']:
            if issue['status']=='patched_pending_review' and all(r in seen and s['source_checks'].get(r,{}).get('status')=='represented' for r in set(issue.get('patched_refs',[])+[issue['target_ref']])):
                issue['status']='model_checked_repair_pending_offline';issue['offline_semantic_repair_verified']=False
            if issue['required_action']=='retrieve' and issue['status']=='reselected_pending_review':
                replacements=issue.get('replacement_mapping_refs',[]);actual_queries={q['query_id']:q for q in s['queries']}
                actual_receipt=bool(issue.get('retrieval_receipts')) and all(row['query_ids'] and all(qid in actual_queries and actual_queries[qid]['receipt_ref']==row['receipt_ref'] for qid in row['query_ids']) for row in issue['retrieval_receipts'])
                reviewed=bool(replacements) and all(ref in seen and s['mapping_checks'].get(ref,{}).get('status')=='no_issue_found' and next(m for m in s['mappings'] if m['mapping_ref']==ref)['status'] in ('proposed_equivalent','proposed_partial') for ref in replacements)
                if actual_receipt and reviewed:issue['status']='model_checked_repair_pending_offline';issue['offline_semantic_repair_verified']=False
        log(s,mode,accepted,rejected,{'requested_count':len(requested),'actually_checked_count':len(seen)})
    except (ValueError,TypeError,KeyError,IndexError) as e:log(s,mode or 'review',accepted,rejected+[{'code':str(e)}])
    s['call']=None;return output(s)


def abort(state_json):
    s=state_load(state_json)
    if not s['stop']:s['stop']=True;s['stop_reason']='external_node_failed_with_completed_state_preserved';log(s,'abort',rejected=[{'code':'node_failed_no_automatic_retry'}])
    return output(s)


def typed_explanations(s):
    records=[];candidates={b['candidate_ref'] for m in s['mappings'] for b in m['bindings'] if b.get('status')!='invalidated'}
    for ref in sorted(candidates):
        concept=s['catalog'][str(s['candidate_refs'][ref])]
        records.append({'predicate':'concept_metadata','candidate_ref':ref,'concept_id':concept['concept_id'],'fields':{key:copy.deepcopy(concept.get(key)) for key in CONCEPT_FIELDS if key!='concept_id'},'vocabulary_version':concept.get('vocabulary_version'),'eligibility':concept.get('eligibility'),'scope':'exact_stored_fields_only_not_clinical_entailment'})
    seen=set()
    for ref in sorted(candidates):
        for mr in meaning_refs(s,ref):
            bundle=s['meaning_evidence'][mr]
            for edge in bundle['relations']:
                key=sha(edge)
                if key in seen:continue
                seen.add(key)
                records.append({'predicate':'vocabulary_directed_edge','meaning_ref':mr,'source_id':edge['source_id'],'relationship_id':edge['relationship_id'],'target_id':edge['target_id'],'direction':edge['direction'],'path':[{'source_id':edge['source_id'],'relationship_id':edge['relationship_id'],'target_id':edge['target_id']}],'relationship_metadata':{key:copy.deepcopy(edge.get(key)) for key in ('is_hierarchical','defines_ancestry','reverse_relationship_id','relationship_concept_id')},'reverse_edge_verified':False,'validity':{key:copy.deepcopy(edge.get(key)) for key in ('valid_start_date','valid_end_date','invalid_reason')},'source_vocabulary':{key:copy.deepcopy(bundle['source'].get(key)) for key in ('vocabulary_id','vocabulary_version')},'target_vocabulary':{key:copy.deepcopy(edge['target_identity'].get(key)) for key in ('vocabulary_id','vocabulary_version')},'scope':'actual_directed_vocabulary_record_not_freetext_equivalence'})
    for mapping in s['mappings']:
        for binding in mapping['bindings']:
            check=s.get('mapping_checks',{}).get(binding['binding_ref'],{});association=check.get('assertion_checks',{}).get('source_association')
            if binding.get('status') not in ACCEPTED_BINDING_STATUSES|{'proposed_partial'} or check.get('status')!='no_issue_found' or not association or association['verdict']!='supported':continue
            records.append({'predicate':'source_association','binding_ref':binding['binding_ref'],'target_refs':copy.deepcopy(binding['covers']),'candidate_ref':binding['candidate_ref'],'concept_id':s['candidate_refs'][binding['candidate_ref']],'role':binding['role'],'proposed_relation':binding.get('proposed_relation',binding['relation']),'accepted_evidence_strength':binding.get('accepted_evidence_strength'),'model_verdict':association['verdict'],'basis':copy.deepcopy(association['basis']),'strict_semantic_equivalence_verified':False,'scope':'model_checked_association_pending_independent_validation'})
    return records


def finish(state_json):
    s=state_load(state_json);s['explanation_contract']='typed_assertions_e8';residual=[]
    if not s['stop']:s['stop']=True;s['stop_reason']='native_loop_limit_reached' if s['usage']['loop_waves']>=s['budget']['max_loop_waves'] else 'native_flow_ended_before_controller_finish'
    for u in s['inventory']+s['units']:
        c=s['source_checks'].get(u['unit_id'])
        if not c or c['revision']!=s['revisions'][u['unit_id']]:residual.append({'target_ref':u['unit_id'],'kind':'source_not_checked_current_revision'})
        elif c['status']!='represented':residual.append({'target_ref':u['unit_id'],'kind':'source_'+c['status']})
    for row in s.get('unmodeled',[]):residual.append({'pending_ref':row['pending_ref'],'target_ref':row['unit_ref'],'kind':'unmodeled_pending','reason':row['reason'],'origin':row['origin'],'field_path':row['field_path']})
    for u in s['inventory']+s['units']:
        if u['subject'] is None:residual.append({'target_ref':u['unit_id'],'kind':'subject_pending','reason':'explicit_null_preserved_no_subject_inferred'})
    for i in s['issues']:
        if i['kind'] in RATIONALE_KINDS:continue
        if i['status']!='model_checked_repair_pending_offline':residual.append({'issue_ref':i['issue_ref'],'kind':i['status'],'detail':i['detail']})
    for t in s['tasks']:
        if not target_current(s,t):continue
        bs=[b for m in s['mappings'] for b in m['bindings'] if t['target_ref'] in b['covers'] and b.get('status') not in ('invalidated','pending_review')]
        if not bs or any(b.get('status') not in ACCEPTED_BINDING_STATUSES for b in bs):residual.append({'target_ref':t['target_ref'],'kind':'mapping_not_precise_or_unresolved','query_status':t['query_status'],'reason':t.get('unresolved_reason')})
    for blocker in s.get('source_gate',{}).get('blockers',[]):residual.append(dict(blocker,kind='source_prerequisite_incomplete'))
    blockers=completion_blockers(s)
    for b in blockers:
        if b['kind']!='unresolved_feedback':residual.append(copy.deepcopy(b))
    facets=source_facets(s)
    for f in facets:
        t=next((t for t in s['tasks'] if t['unit_id']==f['unit_id'] and t['field_path']==f['field_path'] and target_current(s,t)),None)
        bs=[b for m in s['mappings'] for b in m['bindings'] if t and t['target_ref'] in b['covers'] and b.get('status')!='invalidated']
        f['target_ref']=t['target_ref'] if t else None
        has_residual=bool(t) and any(row['target_ref']==t['target_ref'] for mapping in s['mappings'] if any(b.get('status')!='invalidated' and t['target_ref'] in b['covers'] for b in mapping['bindings']) for row in mapping['residuals'])
        f['coding_status']='context_only' if f['intent']=='context_only' else 'unclassified_preserved' if f['intent']=='unclassified' else 'partial_coverage_with_explicit_residual' if has_residual else 'legacy_proposal_not_revalidated_e8' if bs and any(not b.get('accepted_evidence_strength') for b in bs) else 'proposed_equivalent_pending_offline' if bs and all(b.get('status')=='proposed_equivalent' for b in bs) else 'label_association_pending_offline' if bs and all(b.get('status') in ACCEPTED_BINDING_STATUSES for b in bs) else 'partial_or_unresolved'
        if f['intent']=='unclassified':residual.append({'kind':'unclassified_source_facet_preserved','facet_ref':f['facet_ref'],'field_path':f['field_path'],'target_ref':f['unit_id']})
    s['source_facets']=facets
    association_limits=[{'binding_ref':b['binding_ref'],'relation':'label_association','strict_equivalence':'not_claimed','context_dependencies':copy.deepcopy(m['dependencies'])} for m in s['mappings'] for b in m['bindings'] if b.get('status')=='proposed_label_association']
    records=typed_explanations(s)
    public=copy.deepcopy(s);public.update(published_typed_records=records,proposed_reason_trace=[{'mapping_ref':m['mapping_ref'],'proposed_reason':m.get('proposed_reason',m['reason']),'status':'not_independently_reviewed_not_published','withdrawal_is_not_semantic_repair':True} for m in s['mappings']],application_status='finished_with_residuals' if residual or blockers else 'model_checked_pending_offline',completion_gate={'status':'incomplete' if blockers else 'current_checks_recorded_pending_offline','blockers':blockers,'semantic_repair_claimed':False},residuals=residual,association_limits=association_limits,omop_export='not_implemented',independently_confirmed_offline=False,scope='试验性策略更新；同模型后审不等于独立专家或跨病例验证。')
    lines=['证据驱动术语建议；'+public['application_status'],'停止原因：'+str(s['stop_reason']),'','原文：',s['source']['case_text'],'','事实与限定：']
    for u in s['units']:lines.append('- '+u['unit_id']+' r'+str(s['revisions'][u['unit_id']])+' '+u['label']+'｜主体 '+(u['subject'] if u['subject'] is not None else '待核（null）')+'｜'+dump({k:u[k] for k in ('value','unit','site','attributes','relations')}))
    if s.get('unmodeled'):lines+=['','原样隔离的未建模信息（未算已表达，待核）：',dump(s['unmodeled'])]
    lines+=['','概念关联（模型建议）：']
    if s.get('extraction_notes'):lines+=['提取时的补充/未决说明（不能代替事实归属）：',dump(s['extraction_notes'])]
    for m in s['mappings']:
        for binding in m['bindings']:
            if binding.get('status')=='invalidated':continue
            concept=s['catalog'][str(s['candidate_refs'][binding['candidate_ref']])]
            lines.append('- '+binding['binding_ref']+' ['+binding.get('status','pending_review')+'] '+','.join(binding['covers'])+' → '+str(concept['concept_id'])+' '+concept['concept_name']+'｜'+binding['role']+'｜模型提案 '+binding.get('proposed_relation',binding['relation'])+'｜实际证据级别 '+binding.get('accepted_evidence_strength','not_checked'))
    lines+=['','有类型的已记录依据（字段/实际有向边与模型关联分开，不证明严格语义等价）：',dump(records),'','原自由理由仅保留在structured_json.proposed_reason_trace，未经全文独立核验；撤出发布不计语义修复。']
    lines+=['','限定覆盖账本（保全不等于已编码）：',dump(facets)]
    if association_limits:lines+=['','已审标签关联（保留主体/否定/限定；未声称严格断言等价）：',dump(association_limits)]
    lines+=['','实际写回（未作离线语义通过判定）：']+[ '- '+x['target_ref']+' r'+str(x['before_revision'])+'→r'+str(x['before_revision']+1)+' issue '+x['issue_ref'] for x in s['changes']]
    lines+=['','未完成：']+['- '+dump(x) for x in residual]
    lines+=['','有限查询零命中或跳过不表示概念不存在；本输出不是正式OMOP记录或已验证后协调。',public['scope']]
    return {'report':'\n'.join(lines),'structured_json':dump(public),'policy_candidate':dump({'scope':'试验性策略候选；未自动晋升','candidates':[{'failure_kind':i['kind'],'next_action':i['required_action']} for i in s['issues']]})}
