"""Encrypted Work↔Actions handoff. Plaintext never enters public GitHub artifacts."""
import argparse,gzip,hashlib,json,os,sys,zipfile
from pathlib import Path
from cryptography.fernet import Fernet,InvalidToken

MAX_BYTES=50*1024*1024

def cipher(key=None):
 value=key or os.environ.get('SABAH_RAIL_BRIDGE_KEY')
 if not value:raise ValueError('SABAH_RAIL_BRIDGE_KEY missing; private processing locked')
 return Fernet(value.encode() if isinstance(value,str) else value)

def encrypt_bytes(data,key=None):
 if len(data)>MAX_BYTES:raise ValueError('Bridge size limit; divide workload before increasing it')
 return cipher(key).encrypt(gzip.compress(data))

def decrypt_bytes(data,key=None):
 compressed=cipher(key).decrypt(data)
 import io
 with gzip.GzipFile(fileobj=io.BytesIO(compressed))as f:
  plain=f.read(MAX_BYTES+1)
 if len(plain)>MAX_BYTES:raise ValueError('Expanded bridge size exceeds bounded limit')
 return plain

def prepare_request(dataset,request_root,case='Base',year=2045,overrides=None,key=None):
 import uuid
 raw=Path(dataset).read_bytes();payload=json.loads(raw)
 if payload.get('governance_status','').startswith('SYNTHETIC'):raise ValueError('Private bridge requires project evidence, not synthetic demonstration')
 request_id=str(uuid.uuid4());folder=Path(request_root)/request_id;folder.mkdir(parents=True,exist_ok=False)
 encrypted=encrypt_bytes(raw,key);(folder/'input.enc').write_bytes(encrypted)
 manifest={'schema':'sabah-work-bridge/1','request_id':request_id,'data_sha256':hashlib.sha256(raw).hexdigest(),'ciphertext_sha256':hashlib.sha256(encrypted).hexdigest(),'scenario':{'case':case,'year':year,'option':3,'freight_package':'S2','overrides':overrides or {}},'input_policy':'explicit_draft','classification':'Confidential encrypted input; draft baseline alternatives retained'}
 (folder/'request.json').write_text(json.dumps(manifest,indent=2));return folder

def materialize_request(manifest_path,destination,key=None):
 p=Path(manifest_path);m=json.loads(p.read_text())
 if m.get('schema')!='sabah-work-bridge/1':raise ValueError('Unsupported request schema')
 encrypted=(p.parent/'input.enc').read_bytes()
 if hashlib.sha256(encrypted).hexdigest()!=m['ciphertext_sha256']:raise ValueError('Ciphertext checksum mismatch')
 raw=decrypt_bytes(encrypted,key)
 if hashlib.sha256(raw).hexdigest()!=m['data_sha256']:raise ValueError('Input checksum mismatch')
 json.loads(raw);Path(destination).write_bytes(raw);return m

def encrypt_output(directory,destination,key=None):
 import io
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED)as z:
  for p in Path(directory).rglob('*'):
   if p.is_file()and not p.is_symlink():z.write(p,str(p.relative_to(directory)))
 Path(destination).write_bytes(encrypt_bytes(buffer.getvalue(),key))

def decrypt_output(source,destination,key=None):
 import io
 root=Path(destination).resolve();root.mkdir(parents=True,exist_ok=True)
 with zipfile.ZipFile(io.BytesIO(decrypt_bytes(Path(source).read_bytes(),key)))as z:
  if sum(x.file_size for x in z.infolist())>MAX_BYTES:raise ValueError('Output ZIP exceeds extraction bound')
  for info in z.infolist():
   target=(root/info.filename).resolve()
   if root not in target.parents:raise ValueError('Unsafe archive path')
  z.extractall(root)

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 a=sub.add_parser('prepare');a.add_argument('--data',required=True);a.add_argument('--requests',required=True);a.add_argument('--year',type=int,default=2045);a.add_argument('--case',default='Base')
 for name in ['encrypt-output','decrypt-output']:
  a=sub.add_parser(name);a.add_argument('--input',required=True);a.add_argument('--output',required=True)
 a=p.parse_args()
 if a.command=='prepare':print(prepare_request(a.data,a.requests,a.case,a.year))
 elif a.command=='encrypt-output':encrypt_output(a.input,a.output)
 else:decrypt_output(a.input,a.output)

if __name__=='__main__':main()
