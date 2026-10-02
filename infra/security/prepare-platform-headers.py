"""Prepare the known platform vhost; never install files or reload nginx."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

SOURCE = Path('/etc/nginx/sites-available/yleum.ru')
INCLUDE = 'include /etc/nginx/snippets/yleum-platform-https-headers.conf;'
SERVER = re.compile(r'^\s*server_name\s+yleum\.ru\s+www\.yleum\.ru;\s*(?:#.*)?$')
CACHE = re.compile(r'^\s*add_header\s+Cache-Control\s+"(no-cache|public, immutable)"(?:\s+always)?;\s*(?:#.*)?$')

def prepare(value):
    """Known two-server/two-cache shape only; any policy drift needs review."""
    lines=value.splitlines(keepends=True)
    existing=[line for line in lines if line.strip()==INCLUDE]
    clean=[line for line in lines if line.strip()!=INCLUDE]
    active=[line.split('#',1)[0].rstrip() for line in clean]
    servers=[line for line in active if re.search(r'\bserver_name\s',line)]
    headers=[line for line in active if re.search(r'\badd_header\s',line)]
    includes=[line for line in active if re.search(r'\binclude\s',line)]
    if not all(re.fullmatch(r'\s*include /etc/letsencrypt/options-ssl-nginx\.conf;',line) for line in includes):
        raise ValueError('platform_include_shape_changed')
    if len(servers)!=2 or not all(SERVER.fullmatch(line.rstrip()) for line in servers):
        raise ValueError('platform_server_shape_changed')
    if len(headers)!=2 or not all(CACHE.fullmatch(line.rstrip()) for line in headers):
        raise ValueError('platform_header_override_shape_changed')
    if sorted(CACHE.fullmatch(line.rstrip())[1] for line in headers)!=['no-cache','public, immutable']:
        raise ValueError('platform_cache_shape_changed')
    if not re.search(r'(?m)^\s*listen\s+(?:\[::\]:)?443\s+ssl\b',value):
        raise ValueError('platform_tls_listener_missing')
    result=[]
    for line in clean:
        result.append(line)
        if SERVER.fullmatch(line.rstrip()) or CACHE.fullmatch(line.rstrip()):
            indent=line[:len(line)-len(line.lstrip())]
            result.append(indent+INCLUDE+'\n')
    rendered=''.join(result)
    if existing and value!=rendered:
        raise ValueError('partial_or_misplaced_platform_policy')
    return rendered

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare',action='store_true')
    p.add_argument('--expected-sha256')
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if not args.prepare:
        print(json.dumps({'phase':'prepared-only','nginx_mutations':0}));return
    if not args.output or not re.fullmatch('[0-9a-f]{64}',args.expected_sha256 or ''):
        raise SystemExit('expected_source_hash_and_exclusive_output_required')
    if SOURCE.is_symlink() or not SOURCE.is_file(): raise SystemExit('unsafe_vhost_source')
    original=SOURCE.read_bytes()
    if hashlib.sha256(original).hexdigest()!=args.expected_sha256: raise SystemExit('vhost_changed')
    rendered=prepare(original.decode('utf-8')).encode('utf-8')
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(rendered)
    print(json.dumps({'phase':'candidate-written','source_sha256':args.expected_sha256,
                      'candidate_sha256':hashlib.sha256(rendered).hexdigest(),'nginx_mutations':0}))

if __name__=='__main__': main()
