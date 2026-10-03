# Contributor and agent context

Read [README.md](README.md) for scope and usage, then
[OPERATIONS.md](OPERATIONS.md) for design decisions, verification and limitations.
The script's `--man` is the detailed CLI reference. This file owns contribution
rules; keep `CLAUDE.md` as an entry point to them.

## Scope and authority

- Standalone comparison tool intended for public GitHub distribution. Keep it
  independent of cluster repositories, inventories, patch systems and AgentCLI.
- Never add cluster discovery, credential exports, apply, upgrade or other live
  cluster operations to this local comparison tool.
- Respect the requested scope. Editing access does not by itself authorize
  commits, pushes or publication; follow the maintainer's instructions.
- Keep Python >= 3.10 and the standard library as the only Python requirement.
  Preserve the executable entry point and avoid dependency installation.

## Behavior to preserve

- Preserve the CLI contract in `--man` and the
  [design decisions](OPERATIONS.md#design-decisions), including original
  generator output, persistent shared state and the narrow confirmation scope.
- Changes to cache replacement must respect
  [publication and recovery](OPERATIONS.md#cache-publication-and-recovery).
  Do not broaden force to unrelated files or bypass locks and confirmations.
- Keep download authentication, integrity checks and token redaction intact.
  Document behavior changes in their owning reference and update the tests.

## Public content and output

- Code, CLI, documentation and project context use impersonal English. Match
  the maintainer's language in conversation. Use concise ASCII status labels,
  actionable errors on stderr and no emojis or terminal decoration.
- Use synthetic fixtures, example names and documentation IP addresses.
  Never include internal infrastructure names, customer data, inventories,
  real machine configs, credentials, tokens or private runtime artifacts.
- Do not inspect an operator's comparison cache or credentials for routine
  development; follow the isolated test workflow in OPERATIONS.md.
- Preserve the [MIT License](LICENSE) and its approved copyright attribution.
- Retain the approved [team credit](README.md#team-license-and-contact). Record
  actual participants when attributing specific work or verification.

## Verification and records

- Follow [tests and maintenance](OPERATIONS.md#tests-and-maintenance), including
  the lighter checks for documentation-only changes.
- Keep tested evidence separate from supported intent, platform coverage and
  publication status. Passing mocked tests does not prove native platform or
  live authenticated GitHub behavior.
- Update the owning section of OPERATIONS.md when an invariant, limitation or
  verification result changes. Record each fact once and link to it; avoid
  duplicating the manual or preserving a chronological chat transcript.
