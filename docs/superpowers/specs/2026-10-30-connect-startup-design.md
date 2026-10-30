# CONNECT startup transport repair

Recover the authorized owner-console canary's dependency installation without changing
TLS policy, the app's source or business data, or spending another model call to retry
a broken package transport. This is a bounded repair within the ongoing acceptance work.

## Evidence and choice

Node 22.23.3/OpenSSL 3.5.8 sends a 1601-byte ClientHello and times out through the
current trusted proxy. Two isolated falsifiers preserve its default group offers,
including X25519MLKEM768, and certificate validation: paced 384-byte TCP writes and
384-byte TLS records both receive HTTP 200. The exact dropping network element is
unknown; the confirmed trigger is packetization-sensitive startup.

Choose opaque CONNECT startup pacing in the trusted proxy. Client crypto changes
would alter the security policy; more generation retries would pay without fixing
dependency transport. This implementation choice preserves the agreed product behavior.

## Contract

- One 2048-byte prefix budget per CONNECT, shared by leftover body and every read.
- Prefix writes at most 384 bytes; spacing at most 12 ms, cumulative intentional
  pauses at most 72 ms, also accounting for observed scheduler oversleep.
- One absolute 20-second deadline per original send invocation, including its tail;
  separate client-read idle intervals do not spend that write deadline.
- Restore the original socket timeout after each invocation and TCP_NODELAY after
  the prefix. No replay after a failed partial write.
- Preserve exact stream bytes, downstream/HTTP/analytics behavior, checked DNS pins,
  private-address denial, existing idle/overall/byte limits and no-payload logging.
- Register a complete reviewed portable-quiescence successor bundle, retaining all
  historical bundles and rejecting mixed or incomplete source sets.
- Refresh a canary proxy only without active writers/installations: replacement
  preserves its internal address and volumes but terminates old tunnels.

Acceptance requires default unmodified Node through the changed proxy, verified
TLS/public package transfer, normal checks, independent review and canonical delivery.
