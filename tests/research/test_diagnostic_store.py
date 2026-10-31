"""Receipts retain idempotency across crashes, retention and scope changes."""
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest


def store(path, scope='scope-a'):
    from finahinking.research.diagnostic_store import DiagnosticStore
    return DiagnosticStore(path, scope_id=scope)


def ready():
    return {'status':'READY','request_count':0,'duration_ms':0,'usage':{'total_units':0},'cases':[]}


def test_parallel_reservation_owns_one_attempt_and_restart_never_resends(tmp_path):
    path=tmp_path/'diagnostics.sqlite3'
    first=store(path)
    def reserve(_):
        return store(path).reserve('same-key', {'target':'a'}, kind='USER_MODEL')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(reserve, range(6)))
    assert sum(owner for _,owner in results)==1
    ids={row['receipt_id'] for row,_ in results}
    assert len(ids)==1
    original=results[0][0]
    reopened=store(path)
    again,owner=reopened.reserve('same-key', {'target':'a'}, kind='USER_MODEL')
    assert not owner and again['receipt_id']==original['receipt_id'] and again['status']=='UNKNOWN'
    assert again['request_count'] is None and again['usage'] is None
    with pytest.raises(ValueError):
        reopened.reserve('same-key', {'target':'different'}, kind='USER_MODEL')
    first.finish(original['receipt_id'], ready())
    assert reopened.get(original['receipt_id'])['usage']=={'total_units':0}
    with pytest.raises(ValueError):
        first.finish(original['receipt_id'], ready())
    assert store(path, 'other-scope').list()==[]
    with pytest.raises(KeyError):
        store(path, 'other-scope').get(original['receipt_id'])
    if os.name!='nt':
        assert path.stat().st_mode & 0o777 == 0o600


def test_retention_does_not_release_expired_billable_keys(tmp_path):
    db=store(tmp_path/'diag.sqlite3')
    original=None
    for index in range(52):
        receipt,owner=db.reserve('key-'+str(index), {'index':index}, kind='USER_DATA')
        assert owner
        original=original or receipt['receipt_id']
        db.finish(receipt['receipt_id'],ready())
    assert len(db.list(limit=50))==50
    expired,owner=db.reserve('key-0', {'index':0}, kind='USER_DATA')
    assert not owner and expired['status']=='RECORD_EXPIRED'
    assert expired['receipt_id']==original


def test_report_rejects_private_payload_fields_and_corruption_is_read_only(tmp_path):
    path=tmp_path/'diag.sqlite3'
    db=store(path)
    receipt,_=db.reserve('test', {'private':'wire-private-value'}, kind='USER_MODEL')
    for extra in [{'raw_body':'wire-private-value'}, {'endpoint':'https://private.invalid'}, {'cases':[{}]*3000}]:
        with pytest.raises((ValueError,TypeError)):
            db.finish(receipt['receipt_id'],{**ready(),**extra})
    assert 'wire-private-value' not in json.dumps(db.get(receipt['receipt_id']))
    db.finish(receipt['receipt_id'],ready())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE diagnostic_records SET payload='broken-private-body' WHERE receipt_id=?",(receipt['receipt_id'],))
    failed=db.get(receipt['receipt_id'])
    assert failed['status']=='RECORD_INVALID' and 'broken-private' not in json.dumps(failed)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT payload FROM diagnostic_records').fetchone()[0]=='broken-private-body'


def test_store_rejects_symlink(tmp_path):
    target=tmp_path/'elsewhere'
    target.write_bytes(b'unchanged')
    link=tmp_path/'diag.sqlite3'
    link.symlink_to(target)
    with pytest.raises(ValueError):
        store(link)
    assert target.read_bytes()==b'unchanged'
