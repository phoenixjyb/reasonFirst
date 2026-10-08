# Historical launch input

`http_bootstrap.py.txt` is inert reference data for the v0.5.1 maintainer migration
sidecar format. It is copied only into temporary source-inspection fixtures and is
never imported, executed, packaged as a console entry, or installed by production
upgrade code. It contains no operator-specific paths, credentials, or transcripts.

SHA-256 of the LF bytes:
`c964f433d5a36bc571deb3c650cf946e669b302e694d86e6fb3c10d7b53deb9d`.

The original helper prepared this bootstrap alongside the frozen release
`9b0f488ab0dc2e836c10699b2b45c4bfd8009e9b` compatibility modules. Production records
this one supported input fingerprint; it does not trust arbitrary JSON manifests
as equivalent source code or regenerate this legacy launcher. The source profile
only identifies the launch/HTTP wrapper, not its current imported dependency graph.
