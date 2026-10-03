#!/usr/bin/env python3
"""Compare generated Talos MachineConfigs across versions and node roles.

Generates example configs locally; never contacts or changes a cluster.
Reuses cached clients and shared secrets; --force refreshes both.
Preserves complete YAML documents in Vim diff windows.
Run with --help for usage or --man for the full manual.
"""

import argparse
from contextlib import contextmanager
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MANUAL = """NAME
    talosctl_gen_config_vimdiff.py - compare generated Talos MachineConfigs

SYNOPSIS
    talosctl_gen_config_vimdiff.py [-f] VERSION[-VERSION] ROLE[-ROLE] [nc]
    talosctl_gen_config_vimdiff.py -h | -m

DESCRIPTION
    Generate example configs using the selected talosctl releases, then open
    their complete role files in Vim diff windows. No cluster is contacted
    and no configuration is applied. No cluster credentials are used.

ARGUMENTS
    VERSION[-VERSION]
        One or two stable versions >= 1.2.0, in the supplied order.
        Prereleases are not supported.
    ROLE[-ROLE]
        c: control plane; w: worker; c-w or w-c: both roles in that order.
        At most four panes are opened.
    nc
        Hide documentation and examples. Omit nc to include them (default).

OPTIONS
    -h, --help
        Show compact usage and exit.
    -m, --man
        Show this manual and exit. No downloads or cache changes.
    -f, --force
        Download the selected clients and their checksums again, generate new
        shared secrets, and regenerate configs. The replacement files are
        prepared separately: download or generation failures leave the previous
        cache files intact. Successful generation replaces the selected cached
        clients, their configs and the shared secrets.yaml. Other cached client
        versions remain available; their configs refresh on their next use.
        Force does not bypass the cache lock or the format-change confirmation.

EXAMPLES
    talosctl_gen_config_vimdiff.py 1.14.2 c-w
    talosctl_gen_config_vimdiff.py 1.14.0-1.14.2 c nc
    talosctl_gen_config_vimdiff.py 1.13.9-1.14.2 w-c nc
    talosctl_gen_config_vimdiff.py -f 1.14.0-1.14.2 c-w nc

TALOS 1.14 FORMAT CHANGE
    Talos 1.14.0 moves Kubernetes settings and other MachineConfig sections
    into separate YAML documents. Individual documents existed before 1.14.
    The documents stay in controlplane.yaml and worker.yaml, separated by ---.
    All documents and their order are preserved, including with nc.

    Only comparisons crossing from below 1.14.0 to 1.14.0 or newer, in either
    order, show a notice. Press Enter to continue, or Ctrl+C to cancel.
    An interactive terminal is required; EOF and piped input do not confirm.
    Comparisons entirely on either side of the boundary show no notice.

CACHE AND SECRETS
    Directory: $TMPDIR/talosctl-gen-config-vimdiff-<uid>/ (default TMPDIR: /tmp).
    Clients are cached by OS, architecture and version. Cached clients are
    checked locally against their saved SHA-256 and need no network access.
    New downloads are verified against the official release's sha256sum.txt.

    Without force, secrets.yaml is created only when absent and reused across
    versions and runs. Each talosctl reads the bundle during generation; an
    invalid or incompatible bundle stops the comparison. Use force to start
    with fresh comparison secrets and fresh client downloads.

    Configs are regenerated on every run, using the same example cluster name,
    endpoint and shared secrets. Each client keeps its generator defaults.
    Edits to generated configs are scratch work and are replaced on the next
    successful run. All versions must generate successfully before publishing.

    The cache survives exit, but system temporary-file cleanup may remove it.
    Directories use 0700, generated files 0600. Example credentials must never
    be committed or reused for a real cluster. The cache is not a backup.
    A lock prevents concurrent changes while Vim is open. Normal exit and
    handled interrupts remove the lock and staging files. After a forced kill,
    remove a stale .lock only after checking its PID and that no run is active.

REQUIREMENTS
    Python >= 3.10, standard library only; Vim with +diff support.
    Uses vimdiff when available, otherwise vim -d with a notice.
    Linux: amd64 (x86_64), arm64 (aarch64), armv7 (armv7l).
    macOS: amd64 (x86_64), arm64. Other architectures are rejected.
    Downloads need HTTPS access to GitHub and trusted system CA certificates.

ENVIRONMENT
    GITHUB_TOKEN (optional)
        If set, authenticate through the GitHub release API to download clients
        and checksums. Without it, use the public GitHub release download URLs.
        A token for public repository reads is sufficient; write access is not
        needed. Set it in your shell environment or secret manager, for example:

            export GITHUB_TOKEN='<your-github-token>'

        The token is not printed or saved in the cache. Authentication is sent
        only to api.github.com and is not forwarded on redirects to asset hosts.
        On rate-limit errors, the tool explains how to use GITHUB_TOKEN and
        when to retry. Authenticated requests can also be rate limited.
        Existing cached clients still work offline without a token.

EXIT STATUS
    0: comparison completed or help/manual shown.
    1: argument, dependency, download, generation or cache error.
    130: interrupted with Ctrl+C. 143: terminated with SIGTERM.
    Otherwise the editor's exit status is returned.
"""


class ToolError(Exception):
    """An actionable error that does not need a traceback."""


class Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError(f"{message}. Use --help for usage.")


def parse_arguments(argv):
    parser = ArgumentParser(
        usage="%(prog)s [-h] [-m] [-f] VERSION[-VERSION] ROLE[-ROLE] [nc]",
        description="Generate example MachineConfigs and compare them in Vim.\n"
                    "No cluster is contacted and no configuration is applied.",
        formatter_class=lambda prog: argparse.RawDescriptionHelpFormatter(
            prog, max_help_position=24, width=88),
        epilog="Use -m/--man for examples, requirements, cache behavior and exit codes.",
    )
    parser.add_argument("-m", "--man", action="version", version=MANUAL,
                        help="show the full manual and exit")
    parser.add_argument("-f", "--force", action="store_true",
                        help="download clients again and create new shared secrets")
    parser.add_argument("versions", metavar="VERSION[-VERSION]",
                        help="one or two stable versions >= 1.2.0")
    parser.add_argument("roles", metavar="ROLE[-ROLE]",
                        choices=("c", "w", "c-w", "w-c"),
                        help="c: control plane; w: worker; c-w or w-c: both")
    parser.add_argument("nc", metavar="nc", nargs="?", choices=("nc",),
                        help="hide docs/examples (included when omitted)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9]+\.[0-9]+\.[0-9]+)?", args.versions):
        raise ToolError("Use one or two stable versions, e.g. 1.9.0-1.10.0. "
                        "Prereleases are not supported.")
    args.versions = args.versions.split("-")
    for version in args.versions:
        if tuple(map(int, version.split("."))) < (1, 2, 0):
            raise ToolError("Talos versions before 1.2.0 are not supported.")
    roles = {"c": "controlplane", "w": "worker"}
    args.roles = [roles[role] for role in args.roles.split("-")]
    return args


def status(label, message):
    print(f"{label:<8}{message}", flush=True)


def acknowledge_format_change(versions):
    numbers = [tuple(map(int, version.split("."))) for version in versions]
    if not min(numbers) < (1, 14, 0) <= max(numbers):
        return
    print("\nFORMAT CHANGE: Talos 1.14.0\n"
          "\n"
          "This comparison crosses the Talos 1.14.0 configuration format boundary.\n"
          "From 1.14.0, Kubernetes settings move into separate YAML documents,\n"
          "along with other MachineConfig settings. The documents remain inside\n"
          "controlplane.yaml and worker.yaml, separated by ---.\n"
          "\n"
          "Expect moved sections as well as changed values in the diff.\n"
          "All documents are preserved, including with nc.\n", flush=True)
    if not sys.stdin.isatty():
        raise ToolError("An interactive terminal is required to acknowledge this format change. "
                        "Run the comparison in a terminal and press Enter.")
    try:
        input("Press Enter to continue, or Ctrl+C to cancel: ")
    except EOFError as exc:
        raise ToolError("No acknowledgement received; comparison cancelled.") from exc
    print()


def detect_platform():
    system, machine = platform.system(), platform.machine()
    systems = {"Linux": "linux", "Darwin": "darwin"}
    architectures = {"x86_64": "amd64", "amd64": "amd64",
                     "aarch64": "arm64", "arm64": "arm64",
                     "armv7l": "armv7", "armv7": "armv7"}
    if system not in systems:
        raise ToolError(f"Unsupported operating system: {system}. Supported: Linux and macOS.")
    if machine not in architectures:
        raise ToolError(f"Unsupported architecture: {machine}. "
                        "Supported: amd64, arm64 and Linux armv7.")
    arch = architectures[machine]
    if arch == "armv7" and system != "Linux":
        raise ToolError("ARMv7 is supported on Linux only.")
    return f"{systems[system]}-{arch}"


def find_editor():
    if executable := shutil.which("vimdiff"):
        editor = [executable]
    elif executable := shutil.which("vim"):
        editor = [executable, "-d"]
        status("INFO", "vimdiff is missing; using vim -d.")
    else:
        raise ToolError("Neither vimdiff nor vim is installed. "
                        "Install Vim with diff support and retry.")
    result = subprocess.run([executable, "--version"], capture_output=True, text=True)
    if result.returncode:
        raise ToolError(f"Cannot start {executable}. Check the Vim installation.")
    if "+diff" not in result.stdout.split():
        raise ToolError(f"{executable} has no +diff support. Install a full Vim build.")
    return editor


def private_directory(path):
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ToolError(f"Cache path must be an owned directory, not a symlink: {path}")
    path.chmod(0o700)


def check_file(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ToolError(f"Cache path must be an owned regular file, not a symlink: {path}")


@contextmanager
def locked_cache(cache):
    lock = cache / ".lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ToolError(f"Cache is in use: {cache}. Close the other comparison; "
                        "remove a stale .lock only when no run is active.") from exc
    try:
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="ascii")
        with tempfile.TemporaryDirectory(prefix=".stage.", dir=cache) as stage:
            yield Path(stage)
    finally:
        (lock / "pid").unlink(missing_ok=True)
        lock.rmdir()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HTTPSRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ToolError("Refusing a download redirect to a non-HTTPS destination.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def github_token():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if any(not 33 <= ord(character) <= 126 for character in token):
        raise ToolError("GITHUB_TOKEN must contain a single token without whitespace or control characters.")
    return token


def github_http_error(error, authenticated):
    # Inspect a bounded body for GitHub's secondary-limit message; never echo it.
    with error:
        try:
            body = error.read(65536).decode("utf-8", errors="replace").lower()
        except (OSError, HTTPException):
            body = ""
    headers = error.headers or {}
    limited = error.code == 429 or (error.code == 403 and (
        headers.get("X-RateLimit-Remaining") == "0"
        or headers.get("Retry-After") is not None
        or "rate limit" in body or "rate-limit" in body or "abuse detection" in body))
    if limited:
        hint = ("GITHUB_TOKEN is already set; authenticated requests can also be rate limited."
                if authenticated else
                "Set the optional GITHUB_TOKEN environment variable for authenticated downloads.")
        retry = headers.get("Retry-After", "")
        wait = (f"Wait at least {retry} seconds before retrying."
                if retry.isdigit() and len(retry) <= 10 else
                "Wait for GitHub's rate limit to reset before retrying.")
        return ToolError(f"GitHub rate limit reached (HTTP {error.code}). {hint} {wait}")
    if error.code == 401:
        return ToolError("GitHub authentication failed (HTTP 401). Check GITHUB_TOKEN, "
                         "or unset it to use public downloads.")
    if error.code == 403:
        return ToolError("GitHub denied access (HTTP 403). Check GITHUB_TOKEN permissions "
                         "if set, or try again later.")
    if error.code == 404:
        return ToolError("GitHub release or asset not found (HTTP 404). "
                         "Check the version and platform.")
    return ToolError(f"GitHub download failed (HTTP {error.code}). Try again later.")


@contextmanager
def github_response(url, *, token="", accept="application/octet-stream"):
    request = Request(url, headers={"User-Agent": "talosctl_gen_config_vimdiff",
                                   "Accept": accept})
    if token and urlsplit(url).netloc == "api.github.com" and urlsplit(url).scheme == "https":
        # urllib never copies unredirected headers into a redirected request.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with build_opener(HTTPSRedirectHandler()).open(request, timeout=30) as response:
            yield response
    except HTTPError as exc:
        raise github_http_error(exc, bool(token)) from None
    except (URLError, OSError, HTTPException):
        # Do not expose response bodies, signed redirect URLs or request headers.
        raise ToolError("GitHub download failed. Check network access, HTTPS certificates "
                        "and available disk space.") from None


def release_downloads(version, names, token):
    if not token:
        base = f"https://github.com/siderolabs/talos/releases/download/v{version}"
        return {name: f"{base}/{name}" for name in names}
    url = f"https://api.github.com/repos/siderolabs/talos/releases/tags/v{version}"
    with github_response(url, token=token, accept="application/vnd.github+json") as response:
        try:
            release = json.load(response)
        except (ValueError, UnicodeError):
            raise ToolError("GitHub returned invalid release metadata.") from None
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(assets, list):
        raise ToolError("GitHub release metadata contains no asset list.")
    urls = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name, asset_id = asset.get("name"), asset.get("id")
        if name in names and type(asset_id) is int and asset_id > 0:
            # Construct the API URL ourselves rather than trusting a metadata URL.
            urls[name] = f"https://api.github.com/repos/siderolabs/talos/releases/assets/{asset_id}"
    if any(name not in urls for name in names):
        raise ToolError(f"Required client or checksums not found in GitHub release v{version}.")
    return urls


def download(url, destination, *, token=""):
    with github_response(url, token=token) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def run_checked(command, failure, *, cwd=None):
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ToolError(failure) from exc


def get_client(version, platform_dir, stage):
    version_dir = platform_dir / version
    private_directory(version_dir)
    client = version_dir / "talosctl"
    checksum = version_dir / "talosctl.sha256"
    check_file(client)
    check_file(checksum)
    if client.exists() and client.stat().st_size:
        if not checksum.exists():
            raise ToolError(f"Cached checksum is missing: {checksum}. "
                            "Remove this version's cache and retry.")
        expected = checksum.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ToolError(f"Cached checksum is invalid: {checksum}")
        if sha256(client) != expected.lower():
            raise ToolError(f"Checksum mismatch: {client}. "
                            "Remove this version's cache and retry.")
        status("CACHED", f"talosctl {version} ({platform_dir.name})")
    else:
        asset = f"talosctl-{platform_dir.name}"
        status("FETCH", f"talosctl {version} ({platform_dir.name})")
        token = github_token()
        urls = release_downloads(version, (asset, "sha256sum.txt"), token)
        download(urls[asset], stage / asset, token=token)
        download(urls["sha256sum.txt"], stage / "checksums", token=token)
        expected = ""
        for line in (stage / "checksums").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].lstrip("*").removeprefix("./") == asset:
                expected = fields[0]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ToolError(f"Release checksum for {asset} is missing or invalid.")
        if sha256(stage / asset) != expected.lower():
            raise ToolError(f"Downloaded {asset} failed checksum verification.")
        (stage / "client.sha256").write_text(expected.lower() + "\n", encoding="ascii")
        (stage / asset).chmod(0o700)
        (stage / "client.sha256").replace(checksum)
        (stage / asset).replace(client)
    client.chmod(0o700)
    checksum.chmod(0o600)
    run_checked([client, "version", "--client", "--short"],
                f"Cached talosctl cannot run on this system: {client}")
    return client


def shared_secrets(cache, stage, client):
    secrets = cache / "secrets.yaml"
    check_file(secrets)
    if secrets.exists():
        if not secrets.stat().st_size:
            raise ToolError("Cached secrets.yaml is empty. "
                            "Restore it or remove it explicitly before retrying.")
        status("CACHED", "shared comparison secrets")
    else:
        generated = stage / "secrets.yaml"
        run_checked([client, "gen", "secrets", "-o", generated],
                    "Could not generate comparison secrets.")
        if not generated.is_file() or not generated.stat().st_size:
            raise ToolError("Secret generation produced no bundle.")
        generated.replace(secrets)
        status("CREATED", "shared comparison secrets")
    secrets.chmod(0o600)
    return secrets


def generate_configs(args, clients, secrets, stage):
    outputs = []
    for index, (version, client) in enumerate(zip(args.versions, clients)):
        output = stage / f"config-{index}"
        output.mkdir(mode=0o700)
        command = [client, "gen", "config", "talos-vimdiff", "https://192.0.2.1:6443",
                   f"--with-secrets={secrets}", "--with-kubespan"]
        if args.nc:
            command.extend(["--with-docs=false", "--with-examples=false"])
        run_checked(command, f"Config generation failed for {version}. "
                    "Cached secrets were retained; check version compatibility.", cwd=output)
        for role in args.roles:
            config = output / f"{role}.yaml"
            if not config.is_file() or not config.stat().st_size:
                raise ToolError(f"No {role} config generated for {version}.")
        outputs.append(output)
    return outputs


def publish_configs(args, outputs, platform_dir, *, refresh=None):
    # Check destinations before replacing only this tool's generated files.
    moves, files = [], []
    private_directory(platform_dir)
    for version, output in zip(args.versions, outputs):
        version_dir = platform_dir / version
        private_directory(version_dir)
        for name in ("controlplane.yaml", "worker.yaml", "talosconfig"):
            destination = version_dir / name
            if (output / name).is_file():
                (output / name).chmod(0o600)
                moves.append((output / name, destination))
        files.extend(version_dir / f"{role}.yaml" for role in args.roles)
    if refresh is not None:
        for version in dict.fromkeys(args.versions):
            for name in ("talosctl", "talosctl.sha256"):
                moves.append((refresh / platform_dir.name / version / name,
                              platform_dir / version / name))
        moves.append((refresh / "secrets.yaml", platform_dir.parent / "secrets.yaml"))
    for _, destination in moves:
        check_file(destination)
    for source, destination in moves:
        source.replace(destination)
    return files


def compare(args):
    target = detect_platform()
    editor = find_editor()
    acknowledge_format_change(args.versions)
    cache = Path(os.environ.get("TMPDIR") or "/tmp") / f"talosctl-gen-config-vimdiff-{os.getuid()}"
    private_directory(cache)
    cache = cache.resolve()
    with locked_cache(cache) as stage:
        workspace = cache
        if args.force:
            status("FORCE", "Downloading selected clients again and creating new shared secrets.")
            workspace = stage / "refresh"
            private_directory(workspace)
        platform_dir = workspace / target
        private_directory(platform_dir)
        status("CACHE", str(cache))
        clients = [get_client(version, platform_dir, stage) for version in args.versions]
        secrets = shared_secrets(workspace, stage, clients[0])
        outputs = generate_configs(args, clients, secrets, stage)
        files = publish_configs(args, outputs, cache / target,
                                refresh=workspace if args.force else None)
        status("OPEN", f"{len(files)} configuration(s) in Vim diff mode")
        result = subprocess.run([*editor, *files])
        if result.returncode < 0:
            return 128 - result.returncode
        return result.returncode


def handle_termination(signum, _frame):
    raise Interrupted(signum)


def main(argv=None):
    previous_umask = os.umask(0o077)
    previous_term = signal.signal(signal.SIGTERM, handle_termination)
    try:
        if sys.version_info < (3, 10):
            raise ToolError("Python 3.10 or newer is required.")
        return compare(parse_arguments(argv))
    except (ToolError, OSError, UnicodeError) as exc:
        print(f"ERROR   {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, Interrupted) as exc:
        print("STOPPED Comparison interrupted; cached clients and secrets retained.", file=sys.stderr)
        return 128 + (exc.signum if isinstance(exc, Interrupted) else signal.SIGINT)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        os.umask(previous_umask)


if __name__ == "__main__":
    sys.exit(main())
