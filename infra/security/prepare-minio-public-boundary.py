"""Prepare the canonical public MinIO guard; never install or reload nginx."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

SOURCE = Path("/etc/nginx/sites-available/yleum.ru")
GUARDS = (
    "if ($yleum_public_media_readable = 0) { return 403; }",
    "if ($request_method !~ ^(GET|HEAD)$) { return 403; }",
)
LOCATION = re.compile(r"^(\s*)location (?:\^~ )?/minio/ \{\s*$")
INCLUDE_PATHS = {
    "/etc/letsencrypt/options-ssl-nginx.conf",
    "/etc/nginx/snippets/yleum-platform-https-headers.conf",
}
PASSIVE_INCLUDE = {
    "add_header",
    "proxy_hide_header",
    "ssl_session_cache",
    "ssl_session_timeout",
    "ssl_session_tickets",
    "ssl_protocols",
    "ssl_prefer_server_ciphers",
    "ssl_ciphers",
}
SERVER_DIRECTIVES = {
    "listen",
    "server_name",
    "client_max_body_size",
    "ssl_certificate",
    "ssl_certificate_key",
    "ssl_dhparam",
    "ssl_protocols",
    "ssl_ciphers",
    "ssl_prefer_server_ciphers",
    "ssl_session_cache",
    "ssl_session_timeout",
    "ssl_session_tickets",
    "add_header",
    "include",
    "return",
    "access_log",
    "error_log",
}
LOCATION_DIRECTIVES = {
    "proxy_pass",
    "proxy_http_version",
    "proxy_set_header",
    "proxy_read_timeout",
    "proxy_send_timeout",
    "proxy_buffering",
    "proxy_cache",
    "client_max_body_size",
    "expires",
    "add_header",
    "include",
    "allow",
    "deny",
    "root",
    "alias",
    "index",
    "try_files",
    "return",
    "access_log",
    "error_log",
}
PROXIES = {
    "/": "http://127.0.0.1:3100",
    "/api/": "http://127.0.0.1:8200",
    "/api/ws": "http://127.0.0.1:8200",
    "/p/": "http://127.0.0.1:8200",
    "/llm/": "http://127.0.0.1:8101/",
    "/minio/": "http://127.0.0.1:9000/",
}
STATIC_LOCATIONS = {"/otchet", "/otchet/", "/.well-known/acme-challenge/"}
STATIC_ROOTS = {
    "/otchet/": {"/var/www/otchet/"},
    "/.well-known/acme-challenge/": {"/opt/omnia-runtime/acme-webroot", "/var/www/certbot"},
}
MINIO_DIRECTIVES = {
    ("proxy_pass", "http://127.0.0.1:9000/"),
    ("proxy_http_version", "1.1"),
    ("proxy_set_header", "Host", "$host"),
    ("proxy_set_header", "X-Real-IP", "$remote_addr"),
    ("proxy_set_header", "X-Forwarded-For", "$proxy_add_x_forwarded_for"),
    ("proxy_set_header", "X-Forwarded-Proto", "$scheme"),
    ("proxy_buffering", "off"),
    ("proxy_read_timeout", "60s"),
    ("expires", "30d"),
    ("add_header", "Cache-Control", "public, immutable"),
    ("include", "/etc/nginx/snippets/yleum-platform-https-headers.conf"),
}


class Node:
    def __init__(self, words: tuple[str, ...], children: list[Node] | None, start: int):
        self.words = words
        self.children = children
        self.start = start


def parse(value: str) -> list[Node]:
    """Small fail-closed nginx grammar: quote-aware tokens and balanced blocks.

    This is not nginx config validation. Unknown executable/routing directives,
    variables in destinations, nested locations and ambient include contents
    are refused rather than approximated. nginx -T/-t remains an execution gate.
    """
    tokens: list[tuple[str, int, bool]] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char.isspace():
            index += 1
            continue
        if char == "#":
            newline = value.find("\n", index)
            index = len(value) if newline < 0 else newline + 1
            continue
        if char in "{};":
            tokens.append((char, index, True))
            index += 1
            continue
        start = index
        word: list[str] = []
        quote: str | None = None
        while index < len(value):
            char = value[index]
            if quote is None and (char.isspace() or char in "{};#"):
                break
            if char in "\"'":
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None
                else:
                    word.append(char)
            elif char == "\\":
                # Reviewed routes do not need escaped words. Avoid interpreting
                # nginx's escape rules differently from its real lexer.
                raise ValueError("nginx_escaped_token_requires_review")
            else:
                word.append(char)
            index += 1
        if quote is not None or not word:
            raise ValueError("nginx_token_shape_changed")
        tokens.append(("".join(word), start, False))

    position = 0

    def block(nested: bool) -> list[Node]:
        nonlocal position
        nodes: list[Node] = []
        while position < len(tokens):
            words: list[str] = []
            start = tokens[position][1]
            while position < len(tokens) and not tokens[position][2]:
                words.append(tokens[position][0])
                position += 1
            if position == len(tokens):
                raise ValueError("nginx_unterminated_directive")
            punctuation = tokens[position][0]
            position += 1
            if punctuation == "}":
                if words or not nested:
                    raise ValueError("nginx_unbalanced_block")
                return nodes
            if not words:
                raise ValueError("nginx_empty_directive")
            children = block(True) if punctuation == "{" else None
            nodes.append(Node(tuple(words), children, start))
        if nested:
            raise ValueError("nginx_unbalanced_block")
        return nodes

    return block(False)


def included_paths(nodes: list[Node]) -> set[str]:
    result: set[str] = set()
    for node in nodes:
        if node.words[0] == "include":
            if len(node.words) != 2 or node.words[1] not in INCLUDE_PATHS:
                raise ValueError("platform_include_shape_changed")
            result.add(node.words[1])
        if node.children is not None:
            result.update(included_paths(node.children))
    return result


def validate_includes(nodes: list[Node], documents: Mapping[str, str] | None) -> None:
    paths = included_paths(nodes)
    if set(documents or {}) != paths:
        raise ValueError("actual_include_documents_required")
    for path in paths:
        assert documents is not None
        for node in parse(documents[path]):
            if node.children is not None or node.words[0] not in PASSIVE_INCLUDE:
                raise ValueError("inherited_include_routing_requires_review")


def location_path(node: Node) -> str:
    words = node.words
    if len(words) == 2:
        return words[1]
    if len(words) == 3 and words[1] in {"=", "^~"}:
        return words[2]
    raise ValueError("platform_location_shape_changed")


def validate_location(node: Node) -> str:
    path = location_path(node)
    if path not in PROXIES and path not in STATIC_LOCATIONS:
        raise ValueError("unreviewed_route_requires_review")
    if node.children is None:
        raise ValueError("platform_location_shape_changed")
    for child in node.children:
        words = child.words
        if child.children is not None or words[0] not in LOCATION_DIRECTIVES:
            raise ValueError("location_routing_shape_changed")
        if words[0] == "proxy_pass" and words != ("proxy_pass", PROXIES.get(path)):
            raise ValueError("unreviewed_proxy_destination")
        if words[0] in {"root", "alias"} and (
            len(words) != 2 or words[1] not in STATIC_ROOTS.get(path, set())
        ):
            raise ValueError("unreviewed_static_destination")
        if words[0] == "try_files" and words != ("try_files", "$uri", "$uri/", "=404"):
            raise ValueError("unreviewed_internal_redirect")
        if words[0] == "proxy_cache" and words != ("proxy_cache", "off"):
            raise ValueError("unreviewed_proxy_cache")
        if words[0] == "return" and words not in {
            ("return", "301", "/otchet/"),
            ("return", "403"),
            ("return", "404"),
        }:
            raise ValueError("unreviewed_return_destination")
    proxies = [child.words for child in node.children if child.words[0] == "proxy_pass"]
    if path in PROXIES and proxies != [("proxy_pass", PROXIES[path])]:
        raise ValueError("known_proxy_route_required")
    if path == "/minio/" and any(child.words not in MINIO_DIRECTIVES for child in node.children):
        raise ValueError("public_minio_body_shape_changed")
    return path


def is_tls(server: Node) -> bool:
    listens = [node.words for node in server.children or [] if node.words[0] == "listen"]
    if not listens:
        raise ValueError("platform_listener_missing")
    for words in listens:
        if len(words) < 2 or words[1] not in {"80", "[::]:80", "443", "[::]:443"}:
            raise ValueError("unreviewed_platform_listener")
        if words[1] in {"443", "[::]:443"} and (
            "ssl" not in words[2:] or set(words[2:]) - {"ssl", "http2"}
        ):
            raise ValueError("platform_tls_listener_missing")
        if words[1] in {"80", "[::]:80"} and len(words) != 2:
            raise ValueError("unreviewed_platform_listener")
    return any(words[1] in {"443", "[::]:443"} for words in listens)


def validate_servers(nodes: list[Node]) -> Node:
    if len(nodes) != 2 or any(node.words != ("server",) or node.children is None for node in nodes):
        raise ValueError("platform_server_shape_changed")
    tls = [node for node in nodes if is_tls(node)]
    if len(tls) != 1:
        raise ValueError("one_tls_server_required")
    minio: list[tuple[Node, Node]] = []
    for server in nodes:
        children = server.children or []
        names = [node.words for node in children if node.words[0] == "server_name"]
        if names != [("server_name", "yleum.ru", "www.yleum.ru")]:
            raise ValueError("platform_server_shape_changed")
        seen: set[str] = set()
        for node in children:
            if node.words[0] == "location":
                path = validate_location(node)
                if path in seen or server is not tls[0]:
                    raise ValueError("platform_location_scope_changed")
                seen.add(path)
                if path == "/minio/":
                    minio.append((server, node))
            elif node.words[0] == "if":
                # Certbot's HTTP host redirects are the only reviewed if blocks.
                if (
                    server is tls[0]
                    or node.words
                    not in {
                        ("if", "($host", "=", "yleum.ru)"),
                        ("if", "($host", "=", "www.yleum.ru)"),
                    }
                    or node.children is None
                    or len(node.children) != 1
                ):
                    raise ValueError("unreviewed_conditional_route")
                redirect = node.children[0]
                if redirect.children is not None or redirect.words != (
                    "return",
                    "301",
                    "https://$host$request_uri",
                ):
                    raise ValueError("unreviewed_conditional_route")
            elif node.children is not None or node.words[0] not in SERVER_DIRECTIVES:
                raise ValueError("inherited_server_routing_requires_review")
            elif node.words[0] == "return":
                if server is tls[0] or node.words not in {
                    ("return", "301", "https://$host$request_uri"),
                    ("return", "404"),
                }:
                    raise ValueError("unreviewed_return_destination")
    if len(minio) != 1 or minio[0][0] is not tls[0]:
        raise ValueError("public_minio_tls_owner_required")
    return minio[0][1]


def prepare(value: str, *, include_documents: Mapping[str, str] | None = None) -> str:
    """Guard only an actual TLS-owned, balanced, reviewed route graph.

    Unknown routes/destinations, inherited redirects/rewrites and absent or
    executable trusted-include contents stop preparation. Ambient nginx http
    configuration is not contained in this vhost; Root must inspect nginx -T.
    """
    lines = value.splitlines(keepends=True)
    existing = [line for line in lines if line.strip() in GUARDS]
    clean = [line for line in lines if line.strip() not in GUARDS]
    original = "".join(clean)
    nodes = parse(original)
    validate_includes(nodes, include_documents)
    minio = validate_servers(nodes)
    start = original.count("\n", 0, minio.start)
    match = LOCATION.fullmatch(clean[start].split("#", 1)[0].rstrip())
    if match is None:
        raise ValueError("public_minio_location_shape_changed")
    indent = match[1]
    result = clean[:]
    result[start] = indent + "location ^~ /minio/ {\n"
    result[start + 1 : start + 1] = [indent + "    " + guard + "\n" for guard in GUARDS]
    rendered = "".join(result)
    if existing and rendered != value:
        raise ValueError("partial_or_misplaced_minio_guard")
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.prepare:
        print(json.dumps({"phase": "prepared-only", "nginx_mutations": 0}))
        return
    if not args.output or not re.fullmatch("[0-9a-f]{64}", args.expected_sha256 or ""):
        raise SystemExit("expected_source_hash_and_exclusive_output_required")
    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise SystemExit("unsafe_vhost_source")
    original = SOURCE.read_bytes()
    if hashlib.sha256(original).hexdigest() != args.expected_sha256:
        raise SystemExit("vhost_changed")
    try:
        value = original.decode("utf-8")
        documents: dict[str, str] = {}
        include_hashes: dict[str, str] = {}
        for name in sorted(included_paths(parse(value))):
            path = Path(name)
            if path.is_symlink() or not path.is_file():
                raise ValueError("unsafe_include_source")
            content = path.read_bytes()
            documents[name] = content.decode("utf-8")
            include_hashes[name] = hashlib.sha256(content).hexdigest()
        rendered = prepare(value, include_documents=documents).encode("utf-8")
    except (OSError, ValueError, UnicodeError):
        raise SystemExit("vhost_or_include_shape_requires_review") from None
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(rendered)
    print(
        json.dumps(
            {
                "phase": "candidate-written",
                "source_sha256": args.expected_sha256,
                "candidate_sha256": hashlib.sha256(rendered).hexdigest(),
                "include_sha256": include_hashes,
                "scope": "vhost_and_passive_includes_only",
                "nginx_mutations": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
