"""Bounded public-JSON extraction and quote-only lexical recovery.

No eval, field deletion, number/ID synthesis, or truncated-container completion.
Quote recovery never crosses a legal JSON value boundary. A repaired string
followed by another quote on its physical line is conservatively rejected.
This is a deterministic boundary policy, not a global uniqueness proof.
"""
import hashlib as _jr_hashlib
import json as _jr_json
import math as _jr_math
import re as _jr_re

JR_DEFAULT_LIMITS = {'max_chars':100000,'max_depth':64,'max_nodes':10000,
                  'max_edits':32,'max_steps':200000,'max_number_chars':128}


class JR_Error(ValueError):
    def __init__(self, code, position=None):
        super().__init__(code); self.code=code; self.position=position


def _jr_hash(text):
    return _jr_hashlib.sha256(text.encode('utf-8','surrogatepass')).hexdigest()


def _jr_check(value, limits):
    stack=[(value,1)];nodes=0
    while stack:
        item,depth=stack.pop();nodes+=1
        if nodes>limits['max_nodes']:raise JR_Error('node_budget')
        if depth>limits['max_depth']:raise JR_Error('depth_budget')
        if isinstance(item,str):
            if any(0xD800<=ord(c)<=0xDFFF for c in item):raise JR_Error('invalid_unicode')
        elif isinstance(item,float) and not _jr_math.isfinite(item):raise JR_Error('nonfinite_number')
        elif isinstance(item,dict):
            stack.extend((v,depth+1) for pair in item.items() for v in pair)
        elif isinstance(item,list):stack.extend((v,depth+1) for v in item)


def _jr_strict(text,limits):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise JR_Error('duplicate_key')
            result[key]=value
        return result
    def number(value):
        if len(value)>limits['max_number_chars']:raise JR_Error('number_budget')
        return int(value)
    def floating(value):
        if len(value)>limits['max_number_chars']:raise JR_Error('number_budget')
        result=float(value)
        if not _jr_math.isfinite(result):raise JR_Error('nonfinite_number')
        return result
    def constant(_):raise JR_Error('nonfinite_number')
    value=_jr_json.loads(text,object_pairs_hook=pairs,parse_int=number,
                     parse_float=floating,parse_constant=constant)
    _jr_check(value,limits)
    if not isinstance(value,dict):raise JR_Error('top_level_object_required')
    return value


def _jr_unwrap(raw):
    start=len(raw)-len(raw.lstrip());end=len(raw.rstrip());trim=raw[start:end]
    if '```' in trim:
        if trim.count('```')!=2:raise JR_Error('multiple_or_unclosed_fences')
        opening=_jr_re.search(r'(?m)^[ \t]*```(?:json)?[ \t]*\r?\n',raw)
        if opening is None:raise JR_Error('unrecognized_fence')
        closing=_jr_re.search(r'(?m)^[ \t]*```[ \t]*(?:\r?\n|$)',raw[opening.end():])
        if closing is None:raise JR_Error('multiple_or_unclosed_fences')
        body_start=opening.end();body_end=body_start+closing.start()
        after_end=body_start+closing.end()
        outside=raw[:opening.start()]+raw[after_end:]
        if any(c in outside for c in '{}[]'):raise JR_Error('ambiguous_wrapper_structure')
        return raw[body_start:body_end],body_start,body_end,'single_fence'
    if trim.startswith('{'):
        return trim,start,end,None
    first=trim.find('{');last=trim.rfind('}')
    if first<0 or last<first:raise JR_Error('no_complete_object_wrapper')
    outside=trim[:first]+trim[last+1:]
    if any(c in outside for c in '{}[]"'):raise JR_Error('ambiguous_wrapper_structure')
    return trim[first:last+1],start+first,start+last+1,'single_object_wrapper'


class _jr_Parser:
    """Recursive JSON grammar with a conservative quote boundary policy.

    A quote followed by the current container's legal separator is frozen.
    If continuation later fails, we reject rather than escaping that quote and
    swallowing a member or structural delimiter into a string. If the string
    needed repair, another quote later on the same physical line is rejected:
    it could be a field split out of originally damaged string data. This may
    reject otherwise recoverable inline fields; strict JSON bypasses repair.
    """
    number_re=_jr_re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')

    def __init__(self,text,limits):
        self.text=text;self.n=len(text);self.i=0;self.limits=limits
        self.steps=0;self.nodes=0;self.edits=[];self.frozen=[]

    def tick(self,amount=1):
        self.steps+=amount
        if self.steps>self.limits['max_steps']:raise JR_Error('step_budget',self.i)

    def ws(self):
        while self.i<self.n and self.text[self.i] in ' \t\r\n':self.tick();self.i+=1

    def follow(self,index):
        while index<self.n and self.text[index] in ' \t\r\n':self.tick();index+=1
        return self.text[index] if index<self.n else None

    def string(self,terminators,key=False):
        start=self.i;self.i+=1;local=[]
        while self.i<self.n:
            self.tick();ch=self.text[self.i]
            if ch=='\\':
                if self.i+1>=self.n:raise JR_Error('truncated_escape',self.i)
                esc=self.text[self.i+1]
                if esc=='u':
                    digits=self.text[self.i+2:self.i+6]
                    if len(digits)!=4 or not _jr_re.fullmatch('[0-9a-fA-F]{4}',digits):raise JR_Error('invalid_unicode_escape',self.i)
                    self.i+=6;self.tick(5);continue
                if esc not in '"\\/bfnrt':raise JR_Error('invalid_escape',self.i)
                self.i+=2;self.tick();continue
            if ord(ch)<32:raise JR_Error('unescaped_control_character',self.i)
            if ch=='"':
                next_char=self.follow(self.i+1)
                if key or next_char in terminators:
                    end=self.i+1;token=self.text[start:end]
                    if local:
                        probe=end
                        while probe<self.n and self.text[probe] not in '\r\n':
                            self.tick()
                            if self.text[probe]=='"':
                                raise JR_Error('ambiguous_same_line_quote_boundary',probe)
                            probe+=1
                    for offset in reversed(local):token=token[:offset-start]+'\\'+token[offset-start:]
                    value=_jr_json.loads(token)
                    self.frozen.append({'start':start,'end':end,'key':key,'repaired':bool(local)})
                    self.i=end;return value
                if len(self.edits)>=self.limits['max_edits']:raise JR_Error('edit_budget',self.i)
                # A key is never repaired; numeric/literal/container tokens
                # outside a string are never rewritten or manufactured.
                self.edits.append(self.i);local.append(self.i);self.i+=1;continue
            self.i+=1
        raise JR_Error('truncated_string',self.i)

    def value(self,close,depth):
        self.ws();self.tick();self.nodes+=1
        if depth>self.limits['max_depth']:raise JR_Error('depth_budget',self.i)
        if self.nodes>self.limits['max_nodes']:raise JR_Error('node_budget',self.i)
        if self.i>=self.n:raise JR_Error('truncated_value',self.i)
        ch=self.text[self.i]
        if ch=='{':return self.object(depth)
        if ch=='[':return self.array(depth)
        if ch=='"':return self.string({',',close})
        for token,value in [('true',True),('false',False),('null',None)]:
            if self.text.startswith(token,self.i):
                self.i+=len(token)
                if self.follow(self.i) not in {',',close}:raise JR_Error('invalid_literal_boundary',self.i)
                return value
        match=self.number_re.match(self.text,self.i)
        if match:
            token=match.group();self.i=match.end()
            if len(token)>self.limits['max_number_chars']:raise JR_Error('number_budget',self.i)
            if self.follow(self.i) not in {',',close}:raise JR_Error('invalid_number_boundary',self.i)
            return _jr_json.loads(token)
        raise JR_Error('invalid_value_token',self.i)

    def object(self,depth):
        result={};self.i+=1;self.ws()
        if self.i<self.n and self.text[self.i]=='}':self.i+=1;return result
        while True:
            self.ws();self.tick()
            if self.i>=self.n:raise JR_Error('truncated_object',self.i)
            if self.text[self.i]!='"':raise JR_Error('ambiguous_or_invalid_object_key',self.i)
            key=self.string({':'},key=True)
            if key in result:raise JR_Error('duplicate_key',self.i)
            self.ws()
            if self.i>=self.n or self.text[self.i]!=':':raise JR_Error('invalid_object_colon',self.i)
            self.i+=1;result[key]=self.value('}',depth+1);self.ws()
            if self.i>=self.n:raise JR_Error('truncated_object',self.i)
            if self.text[self.i]=='}':self.i+=1;return result
            if self.text[self.i]!=',':raise JR_Error('ambiguous_object_boundary',self.i)
            self.i+=1

    def array(self,depth):
        result=[];self.i+=1;self.ws()
        if self.i<self.n and self.text[self.i]==']':self.i+=1;return result
        while True:
            result.append(self.value(']',depth+1));self.ws()
            if self.i>=self.n:raise JR_Error('truncated_array',self.i)
            if self.text[self.i]==']':self.i+=1;return result
            if self.text[self.i]!=',':raise JR_Error('ambiguous_array_boundary',self.i)
            self.i+=1

    def parse(self):
        self.ws()
        if self.i>=self.n or self.text[self.i]!='{':raise JR_Error('top_level_object_required',self.i)
        value=self.object(1);self.ws()
        if self.i!=self.n:raise JR_Error('multiple_objects_or_trailing_data',self.i)
        _jr_check(value,self.limits)
        if not self.edits:raise JR_Error('no_quote_only_recovery',self.i)
        repaired=self.text
        for offset in reversed(self.edits):repaired=repaired[:offset]+'\\'+repaired[offset:]
        checked=_jr_strict(repaired,self.limits)
        if checked!=value:raise JR_Error('recovery_roundtrip_mismatch')
        return checked,repaired


def recover(raw,limits=None):
    """Return an auditable envelope. Rejection never returns a partial object.

    `normalized_json` is original extracted text plus only audited backslashes,
    not a _jr_json.dumps rewrite of numbers, strings, fields, or ordering.
    """
    configured=dict(JR_DEFAULT_LIMITS)
    result={'ok':False,'status':'rejected','value':None,'normalized_json':None,
      'reason':None,'audit':{'rule_version':'bounded_quote_lexing_v2','rules':[],
        'edits':[],'wrapper':None,'new_model_requests':0,
        'uniqueness_policy':'conservative deterministic boundaries; never reinterpret a legal value delimiter; repaired value rejects any later quote on its physical line; no global uniqueness proof',
        'semantic_repair_claimed':False,'semantic_correctness_verified':False,
        'only_serialization_edits':True}}
    if not isinstance(raw,str):result['reason']='input_must_be_text';return result
    result['audit'].update(raw_sha256=_jr_hash(raw),raw_characters=len(raw))
    try:
        if limits is not None:
            if not isinstance(limits,dict) or set(limits)-set(JR_DEFAULT_LIMITS):raise JR_Error('invalid_limits')
            for key,value in limits.items():
                if type(value) is not int or not 1<=value<=JR_DEFAULT_LIMITS[key]:raise JR_Error('invalid_limits')
                configured[key]=value
        if len(raw)>configured['max_chars']:raise JR_Error('input_budget')
        if any(0xD800<=ord(c)<=0xDFFF for c in raw):raise JR_Error('invalid_unicode')
        try:
            value=_jr_strict(raw,configured)
        except _jr_json.JSONDecodeError:
            pass
        else:
            result.update(ok=True,status='strict',value=value,normalized_json=raw)
            result['audit']['rules']=['strict_json'];result['audit']['normalized_sha256']=_jr_hash(raw);return result
        body,start,end,wrapper=_jr_unwrap(raw)
        result['audit']['extraction']={'body_start':start,'body_end':end,'trim_only':wrapper is None}
        if wrapper:
            result['audit']['wrapper']={'rule':wrapper,'body_start':start,'body_end':end,
              'prefix':raw[:start],'suffix':raw[end:],
              'outside_text_preserved':True,'outside_text_is_not_parsed_fact':True}
            result['audit']['rules'].append(wrapper)
        try:
            value=_jr_strict(body,configured)
        except _jr_json.JSONDecodeError:
            pass
        else:
            result.update(ok=True,status='wrapper',value=value,normalized_json=body)
            result['audit']['normalized_sha256']=_jr_hash(body);return result
        parser=_jr_Parser(body,configured)
        try:value,normalized=parser.parse()
        finally:
            result['audit']['steps']=parser.steps;result['audit']['nodes']=parser.nodes
            result['audit']['frozen_string_boundaries']=parser.frozen
            result['audit']['attempted_quote_offsets']=[start+p for p in parser.edits]
        result['audit']['edits']=[{'operation':'insert','text':'\\','offset':start+p,
          'body_offset':p,'rule':'escape_nonterminal_value_quote',
          'context_sha256':_jr_hash(raw[max(0,start+p-12):min(len(raw),start+p+13)])} for p in parser.edits]
        result['audit']['rules'].append('escape_nonterminal_value_quote')
        result['audit']['normalized_sha256']=_jr_hash(normalized)
        result.update(ok=True,status='recovered',value=value,normalized_json=normalized)
    except JR_Error as exc:
        result['reason']=exc.code;result['audit']['error_position']=exc.position
    except (ValueError,RecursionError,OverflowError) as exc:
        result['reason']='unrecoverable_json_syntax' if not isinstance(exc,RecursionError) else 'depth_budget'
    return result
