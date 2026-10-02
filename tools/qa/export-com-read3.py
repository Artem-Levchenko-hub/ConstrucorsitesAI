"""Prepared-only; encrypt ONLY retained Core3 + Commerce3 own receipt directories."""
import argparse
import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

CERT = '-----BEGIN CERTIFICATE-----\nMIIEKzCCApOgAwIBAgIUfTbqbVGhUi9LiUVI3dklFgaya1MwDQYJKoZIhvcNAQEL\nBQAwJTEjMCEGA1UEAwwaeWxldW0tcWEtZXZpZGVuY2UtMjAyNjEwMDEwHhcNMjYx\nMDAxMTQyMzQ1WhcNMjYxMDA0MTQyMzQ1WjAlMSMwIQYDVQQDDBp5bGV1bS1xYS1l\ndmlkZW5jZS0yMDI2MTAwMTCCAaIwDQYJKoZIhvcNAQEBBQADggGPADCCAYoCggGB\nAM7pocztzKgwhU6ulfoL95rE0YGAxEumWlh2tWobLgp98W4egPcr61gop0+sHpN5\n/5eKaGkedN0JtylPGKx720LNYmvThdS3sydP2tP79ebTsyESCMX6YOw5xhO5Voao\nGP6UtoRUjXYvSITsetpvBoBkg/PBG5nEXrSi5dhCYFJqjI1FdEo5pOeoYxxeSOAc\n8IkM89ds8uLVhY3GJjHtnWL5xGsQntvIasGDAYbrdFCpb61aunDUKLmBwyAxaKBy\nu1tF6a8cnF7BiszSimTEpYn5+LtKxxLey8AvfMRPwxrDpTyVtjuuEmHB6eaMvnft\n5FY3Dpac69ONkvBQq1wePMeClMG0d9y5WW8mOijz3Wp+PtuZiJ8a1V7uVofaFyIc\nypM5d2jtR7PAJnrsZHwzsT8YwfR6gDXKv7JsdPtpW6JHgpJxe24bFjfdY160J++C\ntEv/wwTilC2kOj1iWxKzxnZxLqHTshmTo2Iy5VqInUndEwGA4kN7uvlfK6PrWT8l\nnwIDAQABo1MwUTAdBgNVHQ4EFgQUtZi6w+/jyHMNZPnMCFU5KL4jkMwwHwYDVR0j\nBBgwFoAUtZi6w+/jyHMNZPnMCFU5KL4jkMwwDwYDVR0TAQH/BAUwAwEB/zANBgkq\nhkiG9w0BAQsFAAOCAYEAlhsjchTXNRDhvtEjdxWhfTDF1Dy/3xi+YOgjcebgXWca\n8eq0kLNdcsl5AqZjfU6FpTlt474xo/OiJHNLUvpUo4rtvlHBtYPAXmrRK/2QC/5P\nbT0HrAdqCtkvsz3VZqwBFEj3+RAh1PZxfWcJnyoIM/SCrHzR/pEw2V+HvXoQPu1Y\n34MciPGrJhkwoyblSSoYc2S2XnnT02ptWYlEcqfpvuVt4ZHlBKJWVi/Lcg8GgiUh\njpvkuhEGr20sZQxbc9G3ILrLgmUKi9pSlDgDYzwvNbI8oS7EbD98dEyeghO/IEKO\ni20YOCxgNp3wJRFvtMXP40+N160y2X/B9oziRWO6DOWdMWXNXSv0nT3wM04pV3k9\nuXgFZ0tOS7/Rt1rB4PM6LIVdsN16ZhxXdUQljNdaepJYMO2jK8gsDwWB2iQgwjNM\nW5ruBvvSEGDK5i9F2UnRI7pVaewCMVah5OJ4kArc8E41UhQY9DnYyjZjmG7Ehe45\n8LVKGl19ZREtuJDp+R1N\n-----END CERTIFICATE-----\n'
CORE_ROOT = '/tmp/qa-c49-commerce-core3'
COMMERCE_ROOT = '/tmp/qa-c49-commerce-read3'
OUTPUT = Path('/tmp/qa-c49-commerce-export3')
FILE_LIMIT = 2 * 1024**2
TOTAL_LIMIT = 16 * 1024**2
CIPHER_LIMIT = 20 * 1024**2
REMOTE_LIMIT = 30 * 1024**2


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def allowed_names(kind):
    phases = ('API_PLACEMENT_READ', 'DRAIN_STATUS', 'COMMERCE_READ') if kind == 'core' else (
        'SERVICE_IDENTITY', *('READ_' + str(i) for i in range(1, 19)))
    names = {phase + suffix for phase in phases for suffix in ('.start.json', '.stdout', '.stderr', '.result.json')}
    names.add('routed-read-ack.private.json')
    if kind == 'commerce':
        names.add('two-store-diagnostic.private.json')
    return names


def read_receipts(root, kind):
    expected = CORE_ROOT if kind == 'core' else COMMERCE_ROOT
    if root != expected or kind not in {'core', 'commerce'}:
        raise ValueError('Fixed receipt directory required')
    tmp = os.open('/tmp', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(Path(root).name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tmp)
    finally:
        os.close(tmp)
    try:
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise ValueError('Owned private directory required')
        names = sorted(os.listdir(fd))
        if not names or len(names) > 96 or any(name not in allowed_names(kind) for name in names):
            raise ValueError('Unexpected receipt entries')
        result = {}
        total = 0
        for name in names:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(file_fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600 or before.st_uid != os.geteuid() or before.st_size > FILE_LIMIT:
                    raise ValueError('Unsafe receipt file')
                raw = stream.read(FILE_LIMIT + 1)
                after = os.fstat(stream.fileno())
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if len(raw) > FILE_LIMIT or len(raw) != before.st_size or identity(before) != identity(after):
                    raise ValueError('Receipt changed during read')
            total += len(raw)
            if total > TOTAL_LIMIT:
                raise ValueError('Receipt total exceeds bound')
            result[Path(root).name + '/' + name] = raw
        if sorted(os.listdir(fd)) != names:
            raise ValueError('Receipt directory changed during read')
        return result, total
    finally:
        os.close(fd)


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, raw in sorted(files.items()):
            if len(raw) > TOTAL_LIMIT:
                raise ValueError('Oversized archive member')
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            member.mode = 0o600
            tar.addfile(member, io.BytesIO(raw))
    raw = output.getvalue()
    if len(raw) > CIPHER_LIMIT:
        raise ValueError('Archive exceeds bound')
    return raw


def encrypt(raw):
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
    certificate = x509.load_pem_x509_certificate(CERT.encode())
    return pkcs7.PKCS7EnvelopeBuilder().set_data(raw).add_recipient(certificate).encrypt(
        Encoding.DER, [pkcs7.PKCS7Options.Binary])


def remote_script():
    # Only this exact retained-receipt reader and PUBLIC certificate travel stdin.
    import inspect
    prelude = 'import base64,hashlib,io,json,os,stat,sys,tarfile\nfrom pathlib import Path\n'
    constants = '\n'.join(name + '=' + repr(globals()[name]) for name in (
        'CERT', 'CORE_ROOT', 'COMMERCE_ROOT', 'FILE_LIMIT', 'TOTAL_LIMIT', 'CIPHER_LIMIT')) + '\n'
    functions = '\n'.join(inspect.getsource(function) for function in (sha, allowed_names, read_receipts, archive, encrypt))
    ending = '''
try:
 files,total=read_receipts(COMMERCE_ROOT,'commerce')
 plaintext=archive(files)
 cipher=encrypt(plaintext)
 assert len(cipher)<=CIPHER_LIMIT
 print(json.dumps(dict(status='ENCRYPTED_COMMERCE3_RECEIPTS',cipher_b64=base64.b64encode(cipher).decode(),
  cipher_sha256=sha(cipher),files=len(files),raw_bytes=total,plaintext_archive_sha256=sha(plaintext))))
except Exception as exc:
 print(json.dumps(dict(status='COMMERCE3_EXPORT_BLOCKED',error_type=type(exc).__name__)))
 sys.exit(2)
'''
    return prelude + constants + functions + ending


def commerce_cipher():
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', 'commerce',
            '/opt/omnia/apps/orchestrator/.venv/bin/python', '-E', '-']
    child = subprocess.run(argv, input=remote_script().encode(), capture_output=True, timeout=45, check=False)
    if child.returncode != 0 or len(child.stdout) > REMOTE_LIMIT or len(child.stderr) > 65536:
        raise ValueError('Commerce private export failed')
    data = json.loads(child.stdout)
    if data.get('status') != 'ENCRYPTED_COMMERCE3_RECEIPTS':
        raise ValueError('Commerce encrypted export absent')
    cipher = base64.b64decode(data['cipher_b64'], validate=True)
    if len(cipher) > CIPHER_LIMIT or sha(cipher) != data['cipher_sha256'] or type(data['raw_bytes']) is not int or not 0 <= data['raw_bytes'] <= TOTAL_LIMIT:
        raise ValueError('Commerce cipher receipt differs')
    return cipher, {k: data[k] for k in ('cipher_sha256', 'files', 'raw_bytes', 'plaintext_archive_sha256')}


def write_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())


def execute():
    os.mkdir(OUTPUT, 0o700)  # Any existing attempt is STOP, never overwritten.
    core, core_total = read_receipts(CORE_ROOT, 'core')
    remote, receipt = commerce_cipher()
    if core_total + receipt['raw_bytes'] > TOTAL_LIMIT:
        raise ValueError('Combined retained receipt bytes exceed bound')
    files = dict(core)
    files['commerce/retained-read3.cms'] = remote
    files['commerce/encrypted-receipt-manifest.json'] = json.dumps(receipt, sort_keys=True).encode()
    plaintext = archive(files)
    cipher = encrypt(plaintext)
    if len(cipher) > CIPHER_LIMIT:
        raise ValueError('Final cipher exceeds bound')
    payload = {'message': 'Retain encrypted exact49 owned C Commerce READ_8 failure receipts',
               'branch': 'codex/qa-live-browser-20261001',
               'content': base64.b64encode(base64.b64encode(cipher)).decode()}
    write_private(OUTPUT / 'core-and-commerce3.cms', cipher)
    write_private(OUTPUT / 'github-contents.payload.json', json.dumps(payload).encode())
    write_private(OUTPUT / 'cipher-manifest.json', json.dumps(dict(
        status='ENCRYPTED_CORE_AND_COMMERCE3',cipher_sha256=sha(cipher),cipher_bytes=len(cipher),
        core_files=len(core),commerce_files=receipt['files'],raw_bytes=core_total+receipt['raw_bytes'],
        nested_commerce_cipher_sha256=receipt['cipher_sha256'],accepted=False)).encode())
    print(json.dumps(payload))  # Ciphertext only; no raw receipts or error messages.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'status': 'NONEXECUTING_EXPORT_PREPARATION', 'actual_execution': False}))
        return 0
    try:
        execute()
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'ENCRYPTED_EXPORT_BLOCKED', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
