"""Scoped public receipts with durable no-resend tombstones."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

STATUSES = frozenset({'READY','UNKNOWN','NOT_RUN','NOT_CONFIGURED','UNAUTHORIZED','FORBIDDEN','RATE_LIMITED','NON_JSON','MISSING_FIELDS','SCHEMA_ERROR','STREAM_INTERRUPTED','UNSUPPORTED_STREAM','TIMEOUT','TRANSPORT','PROVIDER_ERROR','RESPONSE_TOO_LARGE','RESOURCE_LIMIT','TARGET_BLOCKED','RETRY_EXHAUSTED','FAILED','RECORD_INVALID','RECORD_EXPIRED'})
_KINDS = frozenset({'LOCAL_COMPATIBILITY','USER_DATA','USER_MODEL'})
_ID = re.compile(r'^diag-[a-f0-9]{32}$')
_DIGEST = re.compile(r'^[a-f0-9]{64}$')
_FIELDS = frozenset({'status','scope','external_account_verified','tls_verified','request_count','duration_ms','usage','estimated_cost','record_count','quality_issue_count','pit_available','missing_fields','cases','suite_version','quality_demo'})
_PRICE_FIELDS = frozenset({'instrument','timestamp','available_at','open','high','low','close','volume','amount','industry','suspended'})


def digest(value):
    encoded = json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if len(encoded)>131072:
        raise ValueError('诊断内容超出范围。')
    return hashlib.sha256(encoded).hexdigest()


def public_report(report):
    if not isinstance(report,dict) or set(report)-_FIELDS or report.get('status') not in STATUSES:
        raise ValueError('诊断记录结构无效。')
    for key in ('request_count','duration_ms','record_count','quality_issue_count'):
        value=report.get(key)
        if value is not None and (type(value) is not int or not 0<=value<=10**9):
            raise ValueError('诊断计数无效。')
    if report.get('scope') not in (None,'USER_ENDPOINT_SAMPLE','LOCAL_COMPATIBILITY') or report.get('external_account_verified') not in (None,False):
        raise ValueError('诊断来源无效。')
    if report.get('tls_verified') not in (None,False) or report.get('estimated_cost') is not None:
        raise ValueError('诊断不能推断费用或传输验证。')
    usage=report.get('usage')
    if usage is not None and (not isinstance(usage,dict) or set(usage)-{'input_units','generated_units','total_units'} or any(type(v) is not int or not 0<=v<=10**9 for v in usage.values())):
        raise ValueError('诊断用量无效。')
    missing=report.get('missing_fields',[])
    if not isinstance(missing,list) or any(type(v) is not str or v not in _PRICE_FIELDS for v in missing) or len(missing)>len(_PRICE_FIELDS):
        raise ValueError('诊断字段列表无效。')
    if report.get('pit_available') not in (None,'AVAILABLE','UNKNOWN','UNAVAILABLE'):
        raise ValueError('诊断可用性无效。')
    cases=report.get('cases',[])
    if not isinstance(cases,list) or len(cases)>64:
        raise ValueError('诊断检查项超出范围。')
    for case in cases:
        if not isinstance(case,dict) or set(case)!={'case_id','expected','observed','status'} or not isinstance(case['case_id'],str) or not re.fullmatch(r'(?:data|openai|deepseek)-[a-z_]{1,32}',case['case_id']):
            raise ValueError('诊断检查项无效。')
        if case['expected'] not in STATUSES or case['observed'] not in STATUSES or case['status']!=('PASS' if case['expected']==case['observed'] else 'FAIL'):
            raise ValueError('诊断检查结果不一致。')
    if report.get('suite_version') not in (None,'interface-compatibility.v1'):
        raise ValueError('诊断版本无效。')
    demos=report.get('quality_demo',[])
    if not isinstance(demos,list) or len(demos)>3:
        raise ValueError('评分示例超出范围。')
    for item in demos:
        if (not isinstance(item,dict) or set(item)!={'case_id','mode','attempted_calls','schema_rate','citation_rate','insufficient_rate'}
                or item['case_id'] not in {'complete','sparse','conflict'} or item['mode']!='SYNTHETIC_SCORE_EXAMPLE'
                or type(item['attempted_calls']) is not int or not 0<=item['attempted_calls']<=6
                or any(item[key] is not None and (type(item[key]) not in (int,float) or not 0<=item[key]<=1) for key in ('schema_rate','citation_rate','insufficient_rate'))):
            raise ValueError('评分示例无效。')
    encoded=json.dumps(report,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
    if len(encoded.encode())>131072:
        raise ValueError('诊断内容超出范围。')
    return json.loads(encoded)


class DiagnosticStore:
    def __init__(self,path,*,scope_id):
        if not isinstance(scope_id,str) or not 1<=len(scope_id)<=128:
            raise ValueError('诊断空间无效。')
        self.path,self.scope_id=Path(path),scope_id
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if self.path.is_symlink():
            raise ValueError('诊断文件不能为符号链接。')
        new=not self.path.exists()
        fd=os.open(self.path,os.O_CREAT|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if hasattr(os,'fchmod'): os.fchmod(fd,0o600)
        finally:
            os.close(fd)
        with self.connect() as db:
            if new: db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS diagnostic_keys (scope_id TEXT NOT NULL,key_digest TEXT NOT NULL,intent_digest TEXT NOT NULL,receipt_id TEXT NOT NULL,kind TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(scope_id,key_digest),UNIQUE(scope_id,receipt_id))')
            db.execute('CREATE TABLE IF NOT EXISTS diagnostic_records (receipt_id TEXT PRIMARY KEY,scope_id TEXT NOT NULL,state TEXT NOT NULL,payload TEXT)')

    @contextmanager
    def connect(self):
        if self.path.is_symlink(): raise ValueError('诊断文件不能为符号链接。')
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally:
            db.close()

    def _find(self,db,receipt_id):
        return db.execute('SELECT k.*,r.state,r.payload FROM diagnostic_keys k LEFT JOIN diagnostic_records r ON r.receipt_id=k.receipt_id AND r.scope_id=k.scope_id WHERE k.scope_id=? AND k.receipt_id=?',(self.scope_id,receipt_id)).fetchone()

    @staticmethod
    def _view(row):
        base={'schema_version':'interface-diagnostic.v1','receipt_id':row['receipt_id'],'kind':row['kind'],'input_digest':row['intent_digest'],'created_at':row['created_at'],'state':row['state'] or 'UNAVAILABLE','status':'UNKNOWN','request_count':None,'duration_ms':None,'usage':None,'estimated_cost':None,'cases':[]}
        if row['state'] is None:
            base.update(status='RECORD_EXPIRED')
        elif row['state']=='FINISHED':
            try:
                base.update(public_report(json.loads(row['payload'])))
            except (ValueError,TypeError,KeyError):
                base.update(state='UNAVAILABLE',status='RECORD_INVALID')
        return base

    def reserve(self,key,intent,*,kind):
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',key) or kind not in _KINDS:
            raise ValueError('诊断请求标识无效。')
        key_hash=digest(key)
        intent_hash=digest({'kind':kind,'intent':intent})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT receipt_id,intent_digest FROM diagnostic_keys WHERE scope_id=? AND key_digest=?',(self.scope_id,key_hash)).fetchone()
            if prior:
                if prior['intent_digest']!=intent_hash: raise ValueError('同一请求标识不能用于不同输入。')
                return self._view(self._find(db,prior['receipt_id'])),False
            if db.execute('SELECT COUNT(*) FROM diagnostic_keys WHERE scope_id=?',(self.scope_id,)).fetchone()[0]>=10000:
                raise ValueError('诊断标识已达到本地容量上限。')
            count=db.execute('SELECT COUNT(*) FROM diagnostic_records WHERE scope_id=?',(self.scope_id,)).fetchone()[0]
            if count>=50:
                oldest=db.execute("SELECT r.receipt_id FROM diagnostic_records r JOIN diagnostic_keys k ON k.receipt_id=r.receipt_id AND k.scope_id=r.scope_id WHERE r.scope_id=? AND r.state='FINISHED' ORDER BY k.created_at,k.receipt_id LIMIT 1",(self.scope_id,)).fetchone()
                if oldest is None: raise ValueError('已有过多未完成诊断，请先读取原记录。')
                db.execute('DELETE FROM diagnostic_records WHERE scope_id=? AND receipt_id=?',(self.scope_id,oldest['receipt_id']))
            receipt='diag-'+uuid.uuid4().hex
            db.execute('INSERT INTO diagnostic_keys VALUES (?,?,?,?,?,?)',(self.scope_id,key_hash,intent_hash,receipt,kind,datetime.now(UTC).isoformat()))
            db.execute("INSERT INTO diagnostic_records VALUES (?,?,'PENDING',NULL)",(receipt,self.scope_id))
            return self._view(self._find(db,receipt)),True

    def finish(self,receipt_id,report):
        encoded=json.dumps(public_report(report),ensure_ascii=False,sort_keys=True)
        with self.connect() as db:
            changed=db.execute("UPDATE diagnostic_records SET state='FINISHED',payload=? WHERE receipt_id=? AND scope_id=? AND state='PENDING'",(encoded,receipt_id,self.scope_id)).rowcount
            if changed!=1: raise ValueError('诊断记录已结束或不属于当前空间。')
        return self.get(receipt_id)

    def get(self,receipt_id):
        if not isinstance(receipt_id,str) or not _ID.fullmatch(receipt_id): raise KeyError('diagnostic record unavailable')
        with self.connect() as db:
            row=self._find(db,receipt_id)
        if row is None: raise KeyError('diagnostic record unavailable')
        return self._view(row)

    def list(self,*,limit=20):
        if type(limit) is not int or not 1<=limit<=50: raise ValueError('诊断分页无效。')
        with self.connect() as db:
            rows=db.execute('SELECT k.*,r.state,r.payload FROM diagnostic_keys k JOIN diagnostic_records r ON r.receipt_id=k.receipt_id AND r.scope_id=k.scope_id WHERE k.scope_id=? ORDER BY k.created_at DESC,k.receipt_id DESC LIMIT ?',(self.scope_id,limit)).fetchall()
        return [self._view(row) for row in rows]
