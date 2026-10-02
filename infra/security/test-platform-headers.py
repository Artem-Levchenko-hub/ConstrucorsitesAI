import importlib.util
import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'infra/security/prepare-platform-headers.py'
INCLUDE = 'include /etc/nginx/snippets/yleum-platform-https-headers.conf;'
VHOST = '''server {
    server_name yleum.ru www.yleum.ru;
    location ^~ /otchet/ {
        add_header Cache-Control "no-cache";
    }
    location / { proxy_pass http://127.0.0.1:3100; }
    location /api/ { proxy_pass http://127.0.0.1:8200; }
    location /minio/ {
        add_header Cache-Control "public, immutable";
    }
    listen 443 ssl;
    include /etc/letsencrypt/options-ssl-nginx.conf;
}
server {
    listen 80;
    server_name yleum.ru www.yleum.ru;
    return 301 https://$host$request_uri;
}
'''

class PlatformHeaderTests(unittest.TestCase):
    def prepare(self, value):
        self.assertTrue(MODULE.exists(), 'controlled vhost preparation is missing')
        spec=importlib.util.spec_from_file_location('platform_headers',MODULE)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.prepare(value)

    def test_basics_are_https_only_and_never_set_framing(self):
        values=ROOT/'infra/security/nginx/platform-header-values.conf'
        headers=ROOT/'infra/security/nginx/platform-https-headers.conf'
        self.assertTrue(values.exists(), 'HTTPS-only platform values are missing')
        self.assertTrue(headers.exists(), 'platform basic headers are missing')
        text=values.read_text()+headers.read_text()
        self.assertIn('max-age=31536000',text)
        self.assertIn('nosniff',text)
        self.assertIn('default "";',text)
        self.assertNotIn('includeSubDomains',text)
        self.assertNotIn('preload',text)
        self.assertNotIn('X-Frame-Options',text)
        self.assertNotIn('Content-Security-Policy',text)

    def test_preparation_covers_server_and_cache_override_locations(self):
        actual=self.prepare(VHOST)
        self.assertEqual(actual.count(INCLUDE),4)
        self.assertEqual(''.join(line for line in actual.splitlines(keepends=True) if line.strip()!=INCLUDE),VHOST)
        for line in ('add_header Cache-Control "no-cache";', 'add_header Cache-Control "public, immutable";'):
            self.assertIn(line+'\n        '+INCLUDE,actual)

    def test_preparation_is_idempotent(self):
        once=self.prepare(VHOST)
        self.assertEqual(self.prepare(once),once)

    def test_foreign_host_is_rejected(self):
        with self.assertRaises(ValueError): self.prepare(VHOST.replace('yleum.ru www.yleum.ru','owned.apps.yleum.ru'))

    def test_new_header_override_requires_review(self):
        with self.assertRaises(ValueError): self.prepare(VHOST.replace('location /api/ {','add_header X-Test yes;\n    location /api/ {'))

    def test_partial_or_misplaced_install_requires_review(self):
        with self.assertRaises(ValueError): self.prepare(VHOST.replace('    listen 80;', '    '+INCLUDE+'\n    listen 80;'))

    def test_inline_foreign_server_or_header_override_requires_review(self):
        with self.assertRaises(ValueError): self.prepare(VHOST+'server { server_name foreign.example; listen 80; }\n')
        with self.assertRaises(ValueError): self.prepare(VHOST.replace('location /api/ {','location /api/ { add_header X-Test yes;'))

    def test_unknown_include_requires_review(self):
        with self.assertRaises(ValueError): self.prepare(VHOST.replace('    listen 443 ssl;', '    include /etc/nginx/snippets/unknown-policy.conf;\n    listen 443 ssl;'))

    def test_generator_uses_same_policy_in_cache_override_locations(self):
        source=(ROOT/'infra/max-k3s/migrate/30-bring-up.sh').read_text()
        self.assertIn('/opt/omnia/infra/security/nginx/platform-header-values.conf',source)
        self.assertIn('/opt/omnia/infra/security/nginx/platform-https-headers.conf',source)
        platform=source.split('cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF')[1].split('\nEOF')[0]
        self.assertEqual(platform.count(INCLUDE),3)

    def test_cli_writes_only_exclusive_private_candidate_and_preserves_source(self):
        spec=importlib.util.spec_from_file_location('platform_headers_cli',MODULE)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'yleum.ru';source.write_text(VHOST)
            output=Path(directory)/'candidate';digest=hashlib.sha256(source.read_bytes()).hexdigest()
            argv=['prepare-platform-headers.py','--prepare','--expected-sha256',digest,'--output',str(output)]
            with patch.object(module,'SOURCE',source),patch('sys.argv',argv),contextlib.redirect_stdout(io.StringIO()):
                module.main()
                with self.assertRaises(FileExistsError): module.main()
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertEqual(source.read_text(),VHOST)
            self.assertEqual(output.read_text(),module.prepare(VHOST))

    def test_cli_changed_hash_and_default_mode_have_no_writes(self):
        spec=importlib.util.spec_from_file_location('platform_headers_cli_guard',MODULE)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'yleum.ru';source.write_text(VHOST)
            output=Path(directory)/'candidate'
            argv=['prepare-platform-headers.py','--prepare','--expected-sha256','0'*64,'--output',str(output)]
            with patch.object(module,'SOURCE',source),patch('sys.argv',argv):
                with self.assertRaisesRegex(SystemExit,'vhost_changed'): module.main()
            self.assertFalse(output.exists())
            with patch.object(module,'SOURCE',Path(directory)/'must-not-read'),patch('sys.argv',['prepare-platform-headers.py']),contextlib.redirect_stdout(io.StringIO()) as stdout:
                module.main()
            self.assertIn('prepared-only',stdout.getvalue())

if __name__=='__main__': unittest.main()
