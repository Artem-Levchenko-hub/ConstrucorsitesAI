"""Pure regressions, without a MinIO endpoint, credentials, or database."""

import hashlib
import importlib.util
import json
import posixpath
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit

import pytest

from yleum_api.core import minio as storage

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "infra/security/prepare-minio-public-boundary.py"
PROJECT = "00000000-0000-4000-8000-000000000001"
HASH = "a" * 32
VHOST = """server {
    server_name yleum.ru www.yleum.ru;
    listen 443 ssl;
    location /api/ { proxy_pass http://127.0.0.1:8200; }
    location /minio/ {
        proxy_pass http://127.0.0.1:9000/;
        add_header Cache-Control "public, immutable";
    }
}
server {
    server_name yleum.ru www.yleum.ru;
    listen 80;
    return 301 https://$host$request_uri;
}
"""


@pytest.fixture
def configured(monkeypatch):
    settings = SimpleNamespace(
        minio_bucket_previews="previews",
        minio_bucket_images="omnia-images",
        minio_bucket_photos="omnia-photos",
        minio_bucket_videos="omnia-videos",
        minio_bucket_projects="projects",
        minio_bucket_backups="backups",
        minio_bucket_task_board="task-board",
        minio_public_legacy_media_keys="",
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(
        storage, "get_minio_client", Mock(side_effect=AssertionError("no storage calls"))
    )
    return settings


def permitted(uri):
    # Only generated anchored literal/character-class regexes are evaluated.
    rules = re.findall(r"^    ~(.+) 1;$", storage.public_media_read_boundary_map(), re.M)
    assert rules
    return any(re.fullmatch(rule, uri) for rule in rules)


@pytest.mark.parametrize(
    "bucket,key",
    [
        ("previews", f"{PROJECT}.png"),
        ("omnia-images", f"{PROJECT}/{HASH}.png"),
        *[
            ("omnia-images", f"max-content/{PROJECT}/{HASH}.{ext}")
            for ext in ("jpg", "png", "webp", "gif")
        ],
        ("omnia-photos", f"{PROJECT}/{HASH}.jpg"),
        ("omnia-videos", f"{PROJECT}/{HASH}.mp4"),
    ],
)
def test_public_images_remain_readable_with_or_without_query_signature(configured, bucket, key):
    path = f"/minio/{bucket}/{key}"
    assert permitted(path)
    signed = path + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=synthetic"
    assert permitted(urlsplit(signed).path)


@pytest.mark.parametrize(
    "path",
    [
        "/minio/omnia-images/exe/task/setup.exe",
        "/minio/omnia-images/exports/source.zip",
        "/minio/omnia-images/source/logo.png",
        "/minio/projects/repos/own.tar.gz",
        "/minio/backups/owned.cms",
        "/minio/task-board/owned.json",
        "/minio/omnia-images/",
        "/minio/omnia-images",
        "/minio/",
        f"/minio/omnia-images/{PROJECT}/{HASH}.svg",
        f"/minio/omnia-images/{PROJECT}/{HASH}.png/source.zip",
    ],
)
def test_public_sources_and_private_buckets_are_denied_even_if_signer_is_root(configured, path):
    assert not permitted(path)
    assert not permitted(
        urlsplit(path + "?X-Amz-Credential=synthetic&X-Amz-Signature=synthetic").path
    )


@pytest.mark.parametrize(
    "path",
    [
        "/minio/omnia-images/%65xe/owned/source.zip",
        "/minio/omnia-images/max-content/../exe/owned/source.zip",
        "/minio/omnia-images/max-content/%2e%2e/exe/owned/source.zip",
        "/minio/omnia-images/exe%2fowned%2fsource.zip",
        "/minio/omnia-images/exe%252fowned%252fsource.zip",
        "/minio/omnia-images/%252e%252e/projects/repos/own.tar.gz",
        f"/minio/omnia-images/{PROJECT}/{HASH}.png%2fsource.zip",
        f"/minio/omnia-images/{PROJECT}/{HASH}.png%252fsource.zip",
    ],
)
def test_encoded_archive_paths_fail_closed_after_one_decode_and_normalization(configured, path):
    normalized = posixpath.normpath(unquote(path))
    assert not permitted(normalized)


def test_map_uses_normalized_uri_and_exact_shared_media_resources(configured):
    assert storage.public_media_read_boundary_map().startswith(
        "map $uri $yleum_public_media_readable {\n    default 0;\n"
    )
    assert "$request_uri" not in storage.public_media_read_boundary_map()
    configured.minio_public_legacy_media_keys = '{"omnia-images":["approved-old/photo.jpg"]}'
    assert permitted("/minio/omnia-images/approved-old/photo.jpg")
    assert not permitted("/minio/omnia-images/approved-old/other.jpg")
    configured.minio_bucket_photos = "omnia-images"
    assert permitted(f"/minio/omnia-images/{PROJECT}/{HASH}.jpg")


def test_collision_and_config_injection_never_render_a_guard(configured):
    configured.minio_bucket_images = "projects"
    with pytest.raises(ValueError):
        storage.public_media_read_boundary_map()
    configured.minio_bucket_images = "media;\nmalicious"
    with pytest.raises(ValueError, match="invalid public media boundary configuration"):
        storage.public_media_read_boundary_map()


def helper():
    spec = importlib.util.spec_from_file_location("minio_boundary_prepare", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_guards_only_public_minio_prefix_and_preserves_proxy_and_other_bytes():
    module = helper()
    value = module.prepare(VHOST)
    assert "location ^~ /minio/ {" in value
    assert "if ($yleum_public_media_readable = 0) { return 403; }" in value
    assert "if ($request_method !~ ^(GET|HEAD)$) { return 403; }" in value
    clean = "\n".join(line for line in value.split("\n") if line.strip() not in module.GUARDS)
    assert clean.replace("location ^~ /minio/ {", "location /minio/ {") == VHOST
    assert module.prepare(value) == value


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.replace("yleum.ru www.yleum.ru", "foreign.example"),
        lambda v: v.replace("127.0.0.1:9000/", "127.0.0.1:9001/"),
        lambda v: v.replace("location /api/", "location /minio/alias/"),
        lambda v: v.replace("location /minio/ {", "location /minio/ { return 200;"),
        lambda v: v.replace(
            "    listen 443 ssl;", "    include /etc/nginx/custom-routes.conf;\n    listen 443 ssl;"
        ),
        lambda v: v.replace("location /minio/ {", "location = /minio/ {"),
    ],
)
def test_changed_scope_is_rejected_instead_of_blindly_inserting_guard(change):
    with pytest.raises(ValueError):
        helper().prepare(change(VHOST))


def test_partial_or_misplaced_guard_is_rejected():
    module = helper()
    with pytest.raises(ValueError):
        module.prepare(
            VHOST.replace("    listen 80;", "    " + module.GUARDS[0] + "\n    listen 80;")
        )


def test_certbot_listener_after_locations_keeps_tls_scope_and_guard():
    late = VHOST.replace("    listen 443 ssl;\n", "").replace(
        "\n}\nserver {", "\n    listen 443 ssl;\n}\nserver {", 1
    )
    assert "location ^~ /minio/ {" in helper().prepare(late)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:9000/",
        "http://127.0.0.1:9000",
        "http://localhost:9000/",
        "http://[::1]:9000/",
        "http://minio:9000/",
        "http://172.18.0.2:9000/",
        "http://private_s3/",
        "http://$backend/",
        "https://storage.example/",
    ],
)
def test_alternate_s3_proxy_routes_never_produce_a_candidate(endpoint):
    source = VHOST.replace(
        "    location /api/", f"    location /s3/ {{ proxy_pass {endpoint}; }}\n    location /api/"
    )
    with pytest.raises(ValueError):
        helper().prepare(source)


@pytest.mark.parametrize(
    "directive",
    [
        "error_page 403 = @old_s3;",
        "error_page 403 /s3/;",
        "recursive_error_pages on;",
        "rewrite ^/minio/(.*)$ /s3/$1 last;",
        "proxy_intercept_errors on;",
        "set $backend minio:9000;",
        "js_content s3.forward;",
        "lua_need_request_body on;",
    ],
)
def test_inherited_error_rewrite_and_dynamic_routes_fail_closed(directive):
    with pytest.raises(ValueError):
        helper().prepare(
            VHOST.replace("    location /api/", "    " + directive + "\n    location /api/")
        )


def test_reviewed_named_error_redirect_bypass_is_rejected():
    source = VHOST.replace(
        "    location /api/",
        """    error_page 403 = @old_s3;
    location @old_s3 {
        rewrite ^/minio/(.*)$ /$1 break;
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:9000;
    }
    location /api/""",
    )
    with pytest.raises(ValueError):
        helper().prepare(source)


def test_minio_scope_uses_balanced_server_ownership_not_server_name_positions():
    source = """server {
    server_name yleum.ru www.yleum.ru;
    listen 80;
    location /minio/ {
        proxy_pass http://127.0.0.1:9000/;
    }
}
server {
    listen 443 ssl;
    server_name yleum.ru www.yleum.ru;
    location / { proxy_pass http://127.0.0.1:3100; }
}
"""
    with pytest.raises(ValueError):
        helper().prepare(source)


def test_includes_require_actual_passive_contents_not_just_trusted_filenames():
    include = "include /etc/nginx/snippets/yleum-platform-https-headers.conf;"
    source = VHOST.replace("    listen 443 ssl;", "    " + include + "\n    listen 443 ssl;")
    with pytest.raises(ValueError):
        helper().prepare(source)


@pytest.mark.parametrize(
    "content",
    [
        "error_page 403 = @old_s3;",
        "rewrite ^ /s3/ last;",
        "include /etc/nginx/custom-s3.conf;",
        "location /s3/ { proxy_pass http://127.0.0.1:9000/; }",
        "proxy_intercept_errors on;",
    ],
)
def test_trusted_include_cannot_hide_an_inherited_s3_bypass(content):
    path = "/etc/nginx/snippets/yleum-platform-https-headers.conf"
    source = VHOST.replace("    listen 443 ssl;", f"    include {path};\n    listen 443 ssl;")
    with pytest.raises(ValueError):
        helper().prepare(source, include_documents={path: content})


def test_actual_passive_header_and_certbot_include_documents_are_accepted():
    header = "/etc/nginx/snippets/yleum-platform-https-headers.conf"
    certbot = "/etc/letsencrypt/options-ssl-nginx.conf"
    source = VHOST.replace(
        "    listen 443 ssl;", f"    include {header};\n    include {certbot};\n    listen 443 ssl;"
    )
    documents = {
        header: (
            "proxy_hide_header Strict-Transport-Security;\n"
            "add_header Strict-Transport-Security $yleum_platform_hsts always;\n"
        ),
        certbot: (
            "ssl_session_cache shared:le_nginx_SSL:10m;\n"
            "ssl_protocols TLSv1.2 TLSv1.3;\n"
            'ssl_ciphers "example{cipher};#passive";\n'
        ),
    }
    candidate = helper().prepare(source, include_documents=documents)
    assert "location ^~ /minio/ {" in candidate
    assert helper().prepare(candidate, include_documents=documents) == candidate


def test_certbot_http_if_and_nonpositional_listener_preserve_balanced_tls_owner():
    source = VHOST.replace("    listen 443 ssl;\n", "").replace(
        "\n}\nserver {", "\n    listen 443 ssl;\n}\nserver {", 1
    )
    source = source.replace(
        "    return 301 https://$host$request_uri;",
        "    if ($host = www.yleum.ru) { return 301 https://$host$request_uri; }\n    return 404;",
    )
    assert "location ^~ /minio/ {" in helper().prepare(source)


def test_quoted_structural_tokens_are_data_not_nginx_block_delimiters():
    nodes = helper().parse('ssl_ciphers "}"; ssl_ciphers "{"; ssl_ciphers ";";')
    assert [node.words for node in nodes] == [
        ("ssl_ciphers", "}"),
        ("ssl_ciphers", "{"),
        ("ssl_ciphers", ";"),
    ]


def test_tls_server_return_cannot_preempt_the_guard_or_break_current_media():
    with pytest.raises(ValueError):
        helper().prepare(
            VHOST.replace(
                "    listen 443 ssl;",
                "    listen 443 ssl;\n    return 301 https://$host$request_uri;",
            )
        )


def test_tracked_canonical_routes_and_headers_remain_eligible():
    generator = (ROOT / "infra/max-k3s/migrate/30-bring-up.sh").read_text()
    platform = generator.split('cat > "/etc/nginx/sites-available/$DOMAIN" <<EOF\n')[1]
    platform = platform.split("\nEOF")[0].replace("\\$", "$").replace("$DOMAIN", "yleum.ru")
    platform = platform.rsplit("\n}", 1)[0] + "\n    listen 443 ssl;\n}\n"
    platform += "server { listen 80; server_name yleum.ru www.yleum.ru; return 404; }\n"
    header = "/etc/nginx/snippets/yleum-platform-https-headers.conf"
    docs = {header: "proxy_hide_header Strict-Transport-Security;\n"}
    candidate = helper().prepare(platform, include_documents=docs)
    assert candidate.count("location ^~ /minio/ {") == 1
    assert "proxy_pass         http://127.0.0.1:8200;" in candidate
    assert "proxy_pass         http://127.0.0.1:8101/;" in candidate


def test_cli_reads_and_hashes_only_reviewed_passive_includes_before_writing(
    tmp_path, monkeypatch, capsys
):
    module = helper()
    header = tmp_path / "header.conf"
    header.write_text("proxy_hide_header Strict-Transport-Security;\n")
    source = tmp_path / "vhost"
    source.write_text(
        VHOST.replace("    listen 443 ssl;", f"    include {header};\n    listen 443 ssl;")
    )
    monkeypatch.setattr(module, "SOURCE", source)
    monkeypatch.setattr(module, "INCLUDE_PATHS", {str(header)})
    output = tmp_path / "candidate"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "sys.argv", ["prepare", "--prepare", "--expected-sha256", digest, "--output", str(output)]
    )
    module.main()
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["include_sha256"] == {
        str(header): hashlib.sha256(header.read_bytes()).hexdigest()
    }
    assert receipt["scope"] == "vhost_and_passive_includes_only"
    assert output.stat().st_mode & 0o777 == 0o600
    header.write_text("error_page 403 = @old_s3;\n")
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--prepare",
            "--expected-sha256",
            digest,
            "--output",
            str(tmp_path / "unsafe-candidate"),
        ],
    )
    with pytest.raises(SystemExit, match="vhost_or_include_shape_requires_review"):
        module.main()
    assert not (tmp_path / "unsafe-candidate").exists()


@pytest.mark.parametrize(
    "source",
    [
        VHOST + "upstream private_s3 { server 127.0.0.1:9000; }\n",
        VHOST + "http { error_page 403 = @old_s3; }\n",
        VHOST.replace(
            "    location /api/", "    location @old_s3 { return 403; }\n    location /api/"
        ),
        VHOST.replace("    listen 80;", "    listen 443 ssl;\n    listen 80;"),
        VHOST + "}\n",
        VHOST.replace("    location /api/", "    location /s3/ {\n    location /api/"),
    ],
)
def test_ambiguous_blocks_named_routes_and_extra_tls_scopes_fail_closed(source):
    with pytest.raises(ValueError):
        helper().prepare(source)


def test_cli_is_no_effect_by_default_and_writes_one_private_exclusive_candidate(
    tmp_path, monkeypatch, capsys
):
    module = helper()
    monkeypatch.setattr("sys.argv", ["prepare"])
    module.main()
    assert "prepared-only" in capsys.readouterr().out
    original = tmp_path / "vhost"
    original.write_text(VHOST)
    output = tmp_path / "candidate"
    monkeypatch.setattr(module, "SOURCE", original)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    monkeypatch.setattr(
        "sys.argv", ["prepare", "--prepare", "--expected-sha256", digest, "--output", str(output)]
    )
    module.main()
    assert output.stat().st_mode & 0o777 == 0o600
    assert original.read_text() == VHOST
    with pytest.raises(FileExistsError):
        module.main()
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--prepare",
            "--expected-sha256",
            "0" * 64,
            "--output",
            str(tmp_path / "other"),
        ],
    )
    with pytest.raises(SystemExit, match="vhost_changed"):
        module.main()


@pytest.mark.parametrize("empty", ['""', "''"])
def test_closed_empty_quoted_argument_is_data_in_real_nginx_grammar(empty):
    module = helper()
    nodes = module.parse(f"map $header $value {{ default {empty}; }}")
    assert nodes[0].words == ("map", "$header", "$value")
    assert nodes[0].children[0].words == ("default", "")


@pytest.mark.parametrize("token", ['"', "'", "\"\"''", '""""', "''''", '"bad\\value"'])
def test_empty_argument_support_keeps_unknown_quote_and_escape_shapes_denied(token):
    with pytest.raises(ValueError):
        helper().parse(f"proxy_set_header Header {token};")


@pytest.mark.parametrize(
    "source", ['"";', "'';", 'server { location /minio/ { proxy_pass ""; } }']
)
def test_empty_directive_or_empty_destination_never_produces_candidate(source):
    with pytest.raises(ValueError):
        helper().prepare(source)


@pytest.mark.parametrize("empty", ['""', "''"])
def test_empty_passive_header_data_preserves_other_routes_and_candidate_bytes(empty):
    module = helper()
    source = VHOST.replace(
        "location /api/ { proxy_pass http://127.0.0.1:8200; }",
        f"location /api/ {{ proxy_pass http://127.0.0.1:8200; proxy_set_header Connection {empty}; }}",
    )
    candidate = module.prepare(source)
    assert f"proxy_set_header Connection {empty};" in candidate
    assert "proxy_pass http://127.0.0.1:8200;" in candidate
    assert "proxy_pass http://127.0.0.1:9000/;" in candidate
    assert candidate.count("location ^~ /minio/ {") == 1
    assert module.prepare(candidate) == candidate


def test_empty_argument_does_not_allow_unknown_header_inside_minio_boundary():
    source = VHOST.replace(
        "proxy_pass http://127.0.0.1:9000/;",
        'proxy_pass http://127.0.0.1:9000/; proxy_set_header Arbitrary "";',
    )
    with pytest.raises(ValueError, match="public_minio_body_shape_changed"):
        helper().prepare(source)
