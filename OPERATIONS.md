# Operations and maintenance

This file owns implementation decisions, verification evidence and known limits.
[README.md](README.md) provides usage; `--man` is the complete CLI reference.
[AGENTS.md](AGENTS.md) owns contribution rules for all participating agents.

## Design decisions

- The script runs each requested release's own `talosctl` binary. It does not
  emulate an older generator using a newer client's `--talos-version` flag.
  This preserves differences in generator defaults, including image versions.
- The first requested client creates the shared comparison secrets bundle.
  `talosctl` reads it; Python does not need a YAML parser.
- Generation uses a fixed example cluster name and documentation endpoint.
  Complete generated files go to Vim without normalization, document sorting
  or semantic merging. This is a text comparison, not a migration tool.
- The cache retains its hyphenated name even though the Python script uses
  underscores. Changing the executable's name must not silently abandon
  existing cached clients and comparison secrets. Older unverified cache
  layouts are not imported automatically.
- For [GitHub authentication](README.md#optional-github-authentication), asset
  requests use `application/octet-stream`. Error bodies are inspected only for
  classification, never printed; HTTP 403 alone does not establish a rate limit.

## Cache publication and recovery

The cache lock covers the complete run, including Vim. Cleanup may remove only
the current run's staging directory and lock; stale-lock handling is in `--man`.

Normal runs may populate missing client and secret files before config
generation. A failure does not undo those additions. Existing generated configs
are replaced only after all requested versions generate successfully.

Force stages all replacement files, leaving existing cache files intact until
downloads and generation succeed. Before publication, all destination files are
checked for ownership and regular-file status; symlinks are rejected. The exact
replacement scope is defined in `--man`.

Final replacement uses individual file renames, not a transaction across the
whole cache. A filesystem failure or forced termination during this phase can
leave a partial refresh; no backup or automatic rollback is created. A successful
force run can rebuild the selected comparison files. Do not describe staging as
an unconditional all-or-nothing guarantee.

An editor error does not roll back already published configs or refreshed secrets.

## Tests and maintenance

For code changes, run syntax checks, inspect affected CLI output and run the
relevant automated checks. Run the full suite after changes spanning generation,
cache publication or downloads:

```sh
python3 -m py_compile talosctl_gen_config_vimdiff.py tests/test_vimdiff.py
python3 talosctl_gen_config_vimdiff.py --help
python3 talosctl_gen_config_vimdiff.py --man
python3 -m unittest discover -s tests -v
```

Tests use private temporary directories, synthetic clients and secrets, mocked
HTTP responses and a noninteractive editor substitute. They must not use an
operator's cache, credentials, cluster or real GitHub token. If a local
`talosctl` is available, an additional offline check generates both roles with
fresh test secrets and verifies that a broken bundle is retained and rejected.
With a 1.14 client it also checks multi-document output with and without `nc`.
Report this check as skipped when no client is available.

For behavior changes, cover successful use and the applicable failure paths:
cache reuse, failed force runs, secret retention, checksums, redirect credentials,
rate limits, format-boundary acknowledgement and editor cleanup. Keep README,
the embedded manual and tests aligned. Update this file with decisive evidence.

Documentation-only changes require reference, consistency and whitespace checks.
Run `git diff --check` once the project is a Git repository; before initialization,
check changed files directly. Do not rerun network or generation workflows solely
to validate prose.

## Verification and known limitations

Latest full verification: **2026-10-03**, performed by Codex under the direction
of Marcel Buehring (Platform Engineer). All **36 tests passed** on Linux amd64
with Python 3.13.5 and local `talosctl` 1.14.2, including the offline generation
check. Python 3.10 syntax was checked separately; this is not a Python 3.10
runtime test. The maintainer reviewed the workflow during development.

- macOS and ARM asset selection is covered by simulated platform tests, not
  native execution on those systems.
- GitHub authentication, rate limits and redirect handling use synthetic HTTP
  tests. Live authenticated downloads with a real token have not been verified.
- Stable versions >= 1.2.0 are accepted; the full release range has not been
  exercised with real clients. Prereleases are deliberately unsupported.
- An older client may reject secrets created by a newer one. Failure preserves
  an existing bundle; force generates a new one using the first selected client.
- YAML and semantic validity are not checked independently after generation
  or Vim edits. Generator success and a readable diff do not establish that a
  configuration is suitable for any real cluster.

## Release preparation

The [MIT License](LICENSE) and public contacts are included in the project.
The local Git repository uses `main`, with `origin` configured for GitHub.
The maintainer handles the initial push and publication. Hosted CI is not
configured; local test results are not hosted CI evidence. Follow the public
content rules in [AGENTS.md](AGENTS.md) when preparing a release.
