"""Decrypt only a QA metadata receipt with a one-time key from hidden TTY input."""
import base64,getpass,json,os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
os.umask(0o077)
packet=base64.b64decode(Path('/tmp/qB.packet').read_bytes(),validate=True)
key=bytes.fromhex(getpass.getpass('One-time QA transport key: '))
assert len(key)==32
raw=AESGCM(key).decrypt(packet[:12],packet[12:],b'OWN_B_8fe8_trust_refresh')
value=json.loads(raw)
assert set(value)=={'fixture','ack','pre_receipt'}
assert value['ack']['sha']=='8fe8cff8bcd004dd61845d9b7d019114b3ae7707'
fd=os.open('/tmp/qB.private.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
del key,raw,value
print('Protected owned QA metadata decrypted; no lifecycle action performed')
