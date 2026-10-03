# talosctl_gen_config_vimdiff

Generate example Talos MachineConfigs with different `talosctl` versions and
compare control-plane and worker configurations in Vim diff windows. This helps
maintain bootstrap templates as generator defaults change. No cluster is
contacted and no configuration is applied.

Developed by the [project team](#team-license-and-contact) for its own cluster
workflows, this tool is a comparison aid. Platform engineers remain responsible
for the correctness and suitability of their MachineConfigs and must review
and validate each configuration before applying it.

The software is provided "as is", without warranty. The authors and copyright
holders disclaim liability as set out in the [MIT License](LICENSE).

Feedback about missing functionality, unsupported configurations or unexpected
results is welcome through the [project contacts](#team-license-and-contact).
Share only sanitized examples; never include credentials or real MC exports.

```sh
./talosctl_gen_config_vimdiff.py 1.13.9-1.14.2 c nc
./talosctl_gen_config_vimdiff.py 1.14.2 c-w
./talosctl_gen_config_vimdiff.py 1.14.2-1.14.0 w-c
./talosctl_gen_config_vimdiff.py -f 1.14.0-1.14.2 c-w nc
```

One or two stable versions >= 1.2.0 can be compared, in the supplied order.
Roles accept `c` (control plane), `w` (worker), `c-w` or `w-c`.
Comments and examples are included by default; add `nc` to disable them.
Only these short forms are accepted. Prerelease versions are rejected explicitly.

| Option | Purpose |
| --- | --- |
| `-h`, `--help` | Compact usage |
| `-m`, `--man` | Full manual with examples, requirements and cache behavior |
| `-f`, `--force` | Download selected clients again and generate new shared secrets |

Talos 1.14.0 moves Kubernetes settings and other MachineConfig sections into
separate YAML documents within each role file. Individual documents existed
earlier. The files reach Vim intact; `nc` only hides comments and examples.
See the [Talos 1.14.0 release notes](https://github.com/siderolabs/talos/releases/tag/v1.14.0).

Only comparisons crossing the 1.14.0 boundary, in either direction, require
Enter to acknowledge the format change before work starts. Ctrl+C cancels.
An interactive terminal is required. Comparisons such as 1.14.x against 1.14.x
show no notice. Run `--man` for the full CLI contract and examples.

## Requirements

- Python >= 3.10, using only the standard library. No additional Python packages
  are required. Run the script directly or pass it to `python3`.
- Linux: x86_64/amd64, aarch64/arm64 or armv7l/armv7.
- macOS: x86_64/amd64 or arm64.
- Vim compiled with `+diff`: `vimdiff`, or `vim -d` as a fallback.
- HTTPS access to GitHub and a working system certificate trust store for
  initial downloads.

Other platforms and ambiguous architecture names such as `arm` are rejected;
there is no implicit ARM64 fallback. Asset names follow the official Talos
[release downloads](https://github.com/siderolabs/talos/releases).

## Optional GitHub authentication

If `GITHUB_TOKEN` is set, the tool uses the authenticated GitHub release API
for client and checksum downloads. Otherwise it uses public release URLs.
Provide the token through your shell environment or secret manager, for example:

```sh
export GITHUB_TOKEN='<your-github-token>'
./talosctl_gen_config_vimdiff.py 1.14.2 c-w nc
```

A token for public repository reads is sufficient; write access is not needed.
The token is not printed or stored in the cache. Authorization is sent only to
`api.github.com` and is removed on redirects to asset hosts. Downloads stay on
HTTPS and retain SHA-256 verification.

Rate-limit errors explain how to use `GITHUB_TOKEN` and when to retry;
authenticated requests can also be limited. Cached clients still work offline.
See [GitHub rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
and [release asset downloads](https://docs.github.com/en/rest/releases/assets).

## Persistent comparison cache

The tool reuses clients and shared secrets in
`${TMPDIR:-/tmp}/talosctl-gen-config-vimdiff-<uid>/`. Client caches are separated
by OS, architecture and version. Configs regenerate each run; Vim edits are
scratch work. The directory survives exit but system temporary-file cleanup
may remove it. Its example credentials must never be committed or used for a
real cluster.

`--force` refreshes the selected clients, shared secrets and configs after all
downloads and generations succeed. It preserves the lock and confirmation
requirements. See `--man` for cache usage and
[publication and recovery](OPERATIONS.md#cache-publication-and-recovery) for
replacement guarantees and failure handling.

## Development

```sh
python3 -m py_compile talosctl_gen_config_vimdiff.py
python3 -m unittest discover -s tests -v
```

See [tests and maintenance](OPERATIONS.md#tests-and-maintenance) for test
isolation and optional real-client checks, and
[verification and limitations](OPERATIONS.md#verification-and-known-limitations)
for the exercised platforms and latest results.

## Project context

Read [AGENTS.md](AGENTS.md) for contribution rules and
[OPERATIONS.md](OPERATIONS.md) for design decisions, verification and limitations.
[CLAUDE.md](CLAUDE.md) points to the same shared instructions.

## Team, license and contact

Project team: Marcel Buehring (Platform Engineer), Codex, Claude and Hermes,
working together through AgentCLI.

[MIT License](LICENSE), copyright 2026 Marcel Buehring.

- Website: [marcel.buehr.ing](https://marcel.buehr.ing)
- Email: [marcel@buehr.ing](mailto:marcel@buehr.ing)
- Matrix: [@marcel:buehr.ing](https://matrix.to/#/@marcel:buehr.ing)
