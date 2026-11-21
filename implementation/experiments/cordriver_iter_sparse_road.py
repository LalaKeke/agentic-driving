"""Load the SparseDrive map record for one exact nuScenes sample token."""
import hashlib
import json


def map_record(mapped, token):
    key=b'"'+token.encode()+b'":'
    where=mapped.find(key)
    if where<0:
        raise KeyError(token)
    start=where+len(key)
    array=mapped[start:start+524288].decode("utf8").lstrip()
    record,_=json.JSONDecoder().raw_decode(array)
    if set(record)!={"scores","labels","vectors"}:
        raise ValueError("Unexpected map payload")
    return list(zip(record["scores"],record["labels"],record["vectors"]))


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1<<22), b''):
            h.update(block)
    return h.hexdigest()
