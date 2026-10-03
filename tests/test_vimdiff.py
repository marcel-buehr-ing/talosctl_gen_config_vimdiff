"""Isolated CLI tests. No cluster, real credentials or interactive editor."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.request import Request

SCRIPT = Path(__file__).resolve().parents[1] / "talosctl_gen_config_vimdiff.py"
REAL_TALOSCTL = shutil.which("talosctl")
SPEC = importlib.util.spec_from_file_location("vimdiff_tool", SCRIPT)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)

CLIENT = '''import hashlib, json, os, sys
from pathlib import Path
args = sys.argv[1:]
version = Path(sys.argv[0]).parent.name
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["client", version, *args]) + "\\n")
if args[:1] == ["version"]:
    print("Client: v" + version)
elif args[:2] == ["gen", "secrets"]:
    if os.environ.get("FAKE_SECRETS_FAIL"):
        sys.exit(1)
    Path(args[args.index("-o") + 1]).write_text("synthetic comparison secrets " + os.urandom(16).hex() + "\\n")
elif args[:2] == ["gen", "config"]:
    if os.environ.get("FAKE_GEN_FAIL") == version:
        sys.exit(1)
    secrets = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--with-secrets="))
    payload = {"version": version, "secrets": hashlib.sha256(Path(secrets).read_bytes()).hexdigest(), "comments": "--with-docs=false" not in args}
    for name in ("controlplane.yaml", "worker.yaml", "talosconfig"):
        Path(name).write_text(json.dumps(payload))
else:
    raise SystemExit("Unexpected command")
'''

EDITOR = '''import json, os, signal, sys
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
if args == ["--version"]:
    print("VIM - Vi IMproved " + os.environ.get("FAKE_DIFF", "+diff"))
else:
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps([name, *args]) + "\\n")
    assert all(Path(arg).is_file() for arg in args if not arg.startswith("-"))
    if os.environ.get("FAKE_TERMINATE"):
        os.kill(os.getppid(), signal.SIGTERM)
    raise SystemExit(int(os.environ.get("FAKE_EDITOR_EXIT", "0")))
'''

# Run the real entrypoint in a subprocess, replacing only platform discovery
# and HTTP responses. Client/editor execution and cache I/O remain real.
RUNNER = '''import hashlib, io, json, os, runpy, sys
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

def open_response(request, timeout):
    assert timeout > 0
    url = request.full_url
    authorized = request.get_header("Authorization") is not None
    if url.startswith("https://api.github.com/"):
        assert request.get_header("Authorization") == "Bearer " + os.environ["GITHUB_TOKEN"].strip()
    else:
        assert not authorized
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps(["download", url]) + "\\n")
        log.write(json.dumps(["request", url, authorized, request.get_header("Accept")]) + "\\n")
    if os.environ.get("FAKE_HTTP_CODE"):
        headers = Message()
        if "FAKE_REMAINING" in os.environ:
            headers["X-RateLimit-Remaining"] = os.environ["FAKE_REMAINING"]
        if "FAKE_RETRY_AFTER" in os.environ:
            headers["Retry-After"] = os.environ["FAKE_RETRY_AFTER"]
        body = os.environ.get("FAKE_HTTP_BODY", "").encode()
        raise HTTPError(url, int(os.environ["FAKE_HTTP_CODE"]), "simulated error", headers, io.BytesIO(body))
    if os.environ.get("FAKE_DOWNLOAD_FAIL"):
        raise URLError("simulated network failure")
    client = Path(os.environ["FAKE_CLIENT"]).read_bytes()
    platforms = ("linux-amd64", "linux-arm64", "linux-armv7", "darwin-amd64", "darwin-arm64")
    assets = ["talosctl-" + platform for platform in platforms] + ["sha256sum.txt"]
    if "/releases/tags/" in url:
        return io.BytesIO(json.dumps({"assets": [{"name": name, "id": index} for index, name in enumerate(assets, 1)]}).encode())
    if url.endswith("sha256sum.txt") or url.endswith("/assets/6"):
        digest = "0" * 64 if os.environ.get("FAKE_BAD_HASH") else hashlib.sha256(client).hexdigest()
        client = "".join(digest + "  talosctl-" + platform + "\\n" for platform in platforms).encode()
    return io.BytesIO(client)

class Opener:
    open = staticmethod(open_response)

script = sys.argv.pop(1)
sys.argv[0] = script
with patch("platform.system", return_value=os.environ["FAKE_OS"]), \\
     patch("platform.machine", return_value=os.environ["FAKE_ARCH"]), \\
     patch("sys.stdin.isatty", return_value=os.environ.get("FAKE_TTY") == "1"), \\
     patch("urllib.request.build_opener", return_value=Opener()):
    runpy.run_path(script, run_name="__main__")
'''


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="vimdiff tests ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.template = self.root / "client-template"
        self.write_program(self.template, CLIENT)
        (self.bin / "python3").symlink_to(sys.executable)
        self.write_program(self.bin / "vimdiff", EDITOR)
        self.runner = self.root / "runner.py"
        self.write_program(self.runner, RUNNER)
        self.env = {
            **os.environ,
            "PATH": str(self.bin),
            "TMPDIR": str(self.root),
            "FAKE_LOG": str(self.log),
            "FAKE_CLIENT": str(self.template),
            "FAKE_OS": "Linux",
            "FAKE_ARCH": "x86_64",
            "GITHUB_TOKEN": "",
        }
        self.cache = self.root / ("talosctl-gen-config-vimdiff-" + str(os.getuid()))

    def write_program(self, path, body):
        path.write_text("#!" + sys.executable + "\n" + body)
        path.chmod(0o700)

    def invoke(self, *args, success=True, input_text=""):
        result = subprocess.run([sys.executable, str(self.runner), str(SCRIPT), *args],
                                env=self.env, cwd=self.root, capture_output=True, text=True,
                                timeout=30, input=input_text)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def calls(self, name):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [entry for line in lines if (entry := json.loads(line))[0] == name]

    def test_help_has_no_runtime_dependencies_or_side_effects(self):
        (self.bin / "vimdiff").unlink()
        result = self.invoke("--help")
        self.assertIn("--man", result.stdout)
        self.assertIn("--force", result.stdout)
        self.assertNotIn("CACHE AND SECRETS", result.stdout)
        self.assertLess(len(result.stdout.splitlines()), 25)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.log.exists())

    def test_manual_is_read_only_even_with_force(self):
        (self.bin / "vimdiff").unlink()
        for option in ("-m", "--man"):
            result = self.invoke("--force", option)
            self.assertIn("CACHE AND SECRETS", result.stdout)
            self.assertIn("EXIT STATUS", result.stdout)
            self.assertIn("-f, --force", result.stdout)
            self.assertIn("GITHUB_TOKEN (optional)", result.stdout)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.log.exists())

    def test_executable_entrypoint_shows_help(self):
        result = subprocess.run([str(SCRIPT), "--help"], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERSION[-VERSION]", result.stdout)
        self.assertFalse(self.cache.exists())

    def test_second_run_reuses_clients_and_secrets_without_downloads(self):
        self.invoke("1.9.0-1.10.0", "c-w")
        secrets = (self.cache / "secrets.yaml").read_bytes()
        self.assertEqual(len(self.calls("download")), 4)
        self.assertEqual(len([c for c in self.calls("client") if c[2:4] == ["gen", "secrets"]]), 1)
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        self.invoke("1.10.0-1.9.0", "w-c", "nc")
        self.assertEqual(len(self.calls("download")), 4)
        self.assertEqual((self.cache / "secrets.yaml").read_bytes(), secrets)
        self.assertEqual(len([c for c in self.calls("client") if c[2:4] == ["gen", "secrets"]]), 1)
        panes = self.calls("vimdiff")[-1][1:]
        self.assertEqual([(Path(p).parent.name, Path(p).name) for p in panes], [
            ("1.10.0", "worker.yaml"), ("1.10.0", "controlplane.yaml"),
            ("1.9.0", "worker.yaml"), ("1.9.0", "controlplane.yaml"),
        ])
        self.assertFalse(json.loads(Path(panes[0]).read_text())["comments"])
        self.assertFalse((self.cache / ".lock").exists())
        self.assertEqual(list(self.cache.glob(".stage.*")), [])
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.cache / "secrets.yaml").stat().st_mode & 0o777, 0o600)

    def test_optional_nc_disables_comments(self):
        for mode, enabled in ((None, True), ("nc", False)):
            with self.subTest(mode=mode):
                args = ["1.9.0", "c"]
                if mode:
                    args.append(mode)
                self.invoke(*args)
                config = self.cache / "linux-amd64/1.9.0/controlplane.yaml"
                self.assertEqual(json.loads(config.read_text())["comments"], enabled)

    def test_format_change_requires_enter_in_both_version_orders(self):
        self.env["FAKE_TTY"] = "1"
        for versions in ("1.13.9-1.14.0", "1.14.2-1.13.9", "1.9.0-1.14.2", "1.13.9-2.0.0"):
            with self.subTest(versions=versions):
                result = self.invoke(versions, "c", "nc", input_text="\n")
                self.assertEqual(result.stdout.count("FORMAT CHANGE: Talos 1.14.0"), 1)
                self.assertIn("Press Enter to continue", result.stdout)
                self.assertLess(result.stdout.index("Press Enter"), result.stdout.index("CACHE"))
                self.assertIn("OPEN", result.stdout)

    def test_no_notice_or_prompt_within_either_format_generation(self):
        for versions in ("1.13.0-1.13.9", "1.14.0-1.14.2", "1.14.2-1.14.0",
                         "1.14.2-1.15.0", "1.14.2", "1.13.9"):
            with self.subTest(versions=versions):
                result = self.invoke(versions, "c")
                self.assertNotIn("FORMAT CHANGE", result.stdout)
                self.assertNotIn("Press Enter", result.stdout)
                self.assertIn("OPEN", result.stdout)

    def test_format_change_without_terminal_stops_before_cache_or_download(self):
        for flags in ((), ("--force",)):
            result = self.invoke(*flags, "1.13.9-1.14.2", "c", success=False, input_text="\n")
            self.assertIn("interactive terminal is required", result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.log.exists())

    def test_format_change_eof_does_not_count_as_acknowledgement(self):
        self.env["FAKE_TTY"] = "1"
        result = self.invoke("1.13.9-1.14.2", "c", success=False)
        self.assertIn("No acknowledgement received", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.log.exists())

    def test_platform_mapping_selects_actual_release_asset_names(self):
        for system, architecture, asset in (
            ("Linux", "x86_64", "linux-amd64"), ("Linux", "aarch64", "linux-arm64"),
            ("Linux", "armv7l", "linux-armv7"), ("Darwin", "arm64", "darwin-arm64"),
            ("Darwin", "x86_64", "darwin-amd64"),
        ):
            with self.subTest(system=system, architecture=architecture):
                self.env.update(FAKE_OS=system, FAKE_ARCH=architecture)
                self.invoke("1.9.0", "c")
                self.assertTrue(self.calls("download")[-2][1].endswith("/talosctl-" + asset))

    def test_unsupported_platforms_stop_before_downloads(self):
        for system, architecture in (("Linux", "arm"), ("Linux", "i686"), ("Linux", "ppc64le"), ("Windows_NT", "x86_64"), ("Darwin", "armv7l")):
            self.env.update(FAKE_OS=system, FAKE_ARCH=architecture)
            result = self.invoke("1.9.0", "c", success=False)
            self.assertIn("ERROR", result.stderr)
            self.assertFalse(self.cache.exists())
            self.assertEqual(self.calls("download"), [])

    def test_missing_vim_stops_before_cache_or_download(self):
        (self.bin / "vimdiff").unlink()
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("Neither vimdiff nor vim is installed", result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.calls("download"), [])

    def test_vim_without_diff_is_rejected(self):
        self.env["FAKE_DIFF"] = "-diff"
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("no +diff support", result.stderr)
        self.assertFalse(self.cache.exists())

    def test_vim_fallback_announces_and_uses_diff_mode(self):
        (self.bin / "vimdiff").rename(self.bin / "vim")
        result = self.invoke("1.9.0", "c")
        self.assertIn("using vim -d", result.stdout)
        self.assertEqual(self.calls("vim")[-1][1], "-d")

    def test_invalid_arguments_never_reach_download_or_editor(self):
        invalid = (
            ("1.14.0-rc.1", "c"), ("1.1.9", "c"), ("1.9.0", "x"),
            ("1.9.0", "c", "garbage"), ("1.9.0", "c", "n"),
            ("1.9.0", "c", "nocomments"), ("1.9.0", "c", "comments"),
            ("1.9.0", "controlplane"), ("1.9.0", "worker"),
            ("1.9.0", "controlplane-worker"), ("1.9.0", "c-c"),
            ("1.9.0", "$(touch injected)"),
        )
        for args in invalid:
            result = self.invoke(*args, success=False)
            self.assertIn("ERROR", result.stderr)
        self.assertFalse(self.cache.exists())
        self.assertFalse((self.root / "injected").exists())
        self.assertFalse(self.log.exists())

    def test_failed_download_is_not_published_as_a_client(self):
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        self.invoke("1.9.0", "c", success=False)
        self.assertFalse((self.cache / "linux-amd64/1.9.0/talosctl").exists())
        self.assertFalse((self.cache / ".lock").exists())
        self.assertEqual(list(self.cache.glob(".stage.*")), [])

    def test_bad_release_checksum_prevents_client_execution(self):
        self.env["FAKE_BAD_HASH"] = "1"
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("checksum verification", result.stderr)
        self.assertEqual(self.calls("client"), [])
        self.assertFalse((self.cache / "linux-amd64/1.9.0/talosctl").exists())

    def test_public_downloads_without_token_send_no_authorization(self):
        self.env.pop("GITHUB_TOKEN")
        self.invoke("1.9.0", "c")
        requests = self.calls("request")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(r[1].startswith("https://github.com/") and not r[2] for r in requests))

    def test_optional_token_uses_authenticated_release_api(self):
        token = "synthetic-github-test-token"
        self.env["GITHUB_TOKEN"] = token
        result = self.invoke("1.9.0", "c")
        requests = self.calls("request")
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(r[1].startswith("https://api.github.com/") and r[2] for r in requests))
        self.assertEqual(requests[0][3], "application/vnd.github+json")
        self.assertTrue(all(r[3] == "application/octet-stream" for r in requests[1:]))
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertTrue(all(token.encode() not in p.read_bytes() for p in self.cache.rglob("*") if p.is_file()))
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        self.env["GITHUB_TOKEN"] = "invalid token; unused offline"
        self.invoke("1.9.0", "c")
        self.assertEqual(len(self.calls("request")), 3)

    def test_rate_limit_errors_explain_optional_token_and_retry(self):
        for code, headers, body in (
            (429, {}, ""),
            (403, {"FAKE_REMAINING": "0"}, ""),
            (403, {"FAKE_RETRY_AFTER": "60"}, ""),
            (403, {}, "You have exceeded a secondary rate limit."),
        ):
            with self.subTest(code=code, headers=headers, body=body):
                self.env.update(FAKE_HTTP_CODE=str(code), FAKE_HTTP_BODY=body, **headers)
                result = self.invoke("1.9.0", "c", success=False)
                self.assertIn("GitHub rate limit reached", result.stderr)
                self.assertIn("optional GITHUB_TOKEN environment variable", result.stderr)
                if headers.get("FAKE_RETRY_AFTER"):
                    self.assertIn("Wait at least 60 seconds", result.stderr)
                self.assertEqual(self.calls("client"), [])
                self.assertFalse((self.cache / ".lock").exists())
                self.assertEqual(list(self.cache.glob(".stage.*")), [])
                for key in headers:
                    del self.env[key]

    def test_http_errors_never_echo_token_or_response_body(self):
        token = "synthetic-sensitive-test-token"
        self.env.update(GITHUB_TOKEN=token, FAKE_HTTP_BODY="do not print " + token)
        for code, message in ((401, "authentication failed"), (403, "denied access"),
                              (404, "not found"), (429, "GITHUB_TOKEN is already set")):
            self.env["FAKE_HTTP_CODE"] = str(code)
            result = self.invoke("1.9.0", "c", success=False)
            self.assertIn(message, result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertNotIn("do not print", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            if code != 429:
                self.assertNotIn("rate limit reached", result.stderr)

    def test_invalid_token_is_rejected_without_echo_or_request(self):
        self.env["GITHUB_TOKEN"] = "synthetic-secret\ninvalid-header"
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("GITHUB_TOKEN must contain a single token", result.stderr)
        self.assertNotIn("synthetic-secret", result.stderr)
        self.assertEqual(self.calls("request"), [])

    def test_modified_cached_client_is_not_executed(self):
        self.invoke("1.9.0", "c")
        (self.cache / "linux-amd64/1.9.0/talosctl").write_text("corrupt")
        count = len(self.calls("client"))
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("Checksum mismatch", result.stderr)
        self.assertEqual(len(self.calls("client")), count)

    def test_generation_failure_preserves_previous_configs_and_secrets(self):
        self.invoke("1.9.0-1.10.0", "c-w")
        before = {p: p.read_bytes() for p in self.cache.rglob("*.yaml")}
        self.env["FAKE_GEN_FAIL"] = "1.10.0"
        self.invoke("1.9.0-1.10.0", "c-w", "nc", success=False)
        self.assertEqual({p: p.read_bytes() for p in self.cache.rglob("*.yaml")}, before)
        self.assertEqual(len(self.calls("vimdiff")), 1)

    def test_force_downloads_selected_clients_and_replaces_shared_secrets(self):
        self.invoke("1.9.0-1.10.0", "c-w")
        previous = (self.cache / "secrets.yaml").read_bytes()
        self.template.write_text(self.template.read_text() + "\n# replacement client\n")
        for count, flag in enumerate(("-f", "--force"), 2):
            with self.subTest(flag=flag):
                result = self.invoke(flag, "1.9.0-1.10.0", "c-w", "nc")
                current = (self.cache / "secrets.yaml").read_bytes()
                self.assertNotEqual(current, previous)
                self.assertEqual(len(self.calls("download")), count * 4)
                self.assertIn("FORCE", result.stdout)
                for version in ("1.9.0", "1.10.0"):
                    directory = self.cache / "linux-amd64" / version
                    self.assertEqual((directory / "talosctl").read_bytes(), self.template.read_bytes())
                    self.assertEqual((directory / "talosctl").stat().st_mode & 0o777, 0o700)
                    for role in ("controlplane", "worker"):
                        config = json.loads((directory / (role + ".yaml")).read_text())
                        self.assertEqual(config["secrets"], hashlib.sha256(current).hexdigest())
                        self.assertFalse(config["comments"])
                self.assertEqual((self.cache / "secrets.yaml").stat().st_mode & 0o777, 0o600)
                self.assertFalse((self.cache / ".lock").exists())
                self.assertEqual(list(self.cache.glob(".stage.*")), [])
                previous = current
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        self.invoke("1.9.0-1.10.0", "c-w")
        self.assertEqual(len(self.calls("download")), 12)
        self.assertEqual((self.cache / "secrets.yaml").read_bytes(), previous)

    def test_failed_force_preserves_previous_cache_files(self):
        self.invoke("1.9.0-1.10.0", "c-w")
        before = {p: p.read_bytes() for p in self.cache.rglob("*") if p.is_file()}
        for variable, value in (("FAKE_DOWNLOAD_FAIL", "1"), ("FAKE_BAD_HASH", "1"),
                                ("FAKE_SECRETS_FAIL", "1"), ("FAKE_GEN_FAIL", "1.10.0"),
                                ("FAKE_HTTP_CODE", "429")):
            with self.subTest(failure=variable):
                self.env[variable] = value
                self.invoke("--force", "1.9.0-1.10.0", "c-w", success=False)
                del self.env[variable]
                self.assertEqual({p: p.read_bytes() for p in self.cache.rglob("*") if p.is_file()}, before)
                self.assertEqual(len(self.calls("vimdiff")), 1)
                self.assertFalse((self.cache / ".lock").exists())
                self.assertEqual(list(self.cache.glob(".stage.*")), [])

    def test_force_repairs_corrupt_selected_cache_and_retains_other_versions(self):
        self.invoke("1.9.0-1.10.0", "c-w")
        other = self.cache / "linux-amd64/1.10.0"
        before = {p: p.read_bytes() for p in other.iterdir()}
        (self.cache / "secrets.yaml").write_bytes(b"")
        (self.cache / "linux-amd64/1.9.0/talosctl").write_text("corrupt")
        self.invoke("-f", "1.9.0", "c-w")
        self.assertEqual((self.cache / "linux-amd64/1.9.0/talosctl").read_bytes(), self.template.read_bytes())
        self.assertTrue((self.cache / "secrets.yaml").stat().st_size)
        self.assertEqual({p: p.read_bytes() for p in other.iterdir()}, before)

    def test_force_rejects_secrets_symlink_before_publishing_any_file(self):
        self.invoke("1.9.0", "c-w")
        configs = {p: p.read_bytes() for p in (self.cache / "linux-amd64/1.9.0").iterdir()}
        external = self.root / "external-secrets.yaml"
        external.write_text("untouched")
        (self.cache / "secrets.yaml").unlink()
        (self.cache / "secrets.yaml").symlink_to(external)
        self.invoke("--force", "1.9.0", "c-w", success=False)
        self.assertEqual({p: p.read_bytes() for p in configs}, configs)
        self.assertEqual(external.read_text(), "untouched")
        self.assertTrue((self.cache / "secrets.yaml").is_symlink())

    def test_cache_lock_prevents_a_second_run_from_changing_files(self):
        self.invoke("1.9.0", "c")
        lock = self.cache / ".lock"
        lock.mkdir()
        (lock / "pid").write_text("12345\n")
        for flags in ((), ("--force",)):
            result = self.invoke(*flags, "1.9.0", "c", success=False)
            self.assertIn("Cache is in use", result.stderr)
        self.assertEqual((lock / "pid").read_text(), "12345\n")
        self.assertEqual(len(self.calls("download")), 2)

    def test_empty_secrets_are_not_replaced(self):
        self.cache.mkdir()
        secrets = self.cache / "secrets.yaml"
        secrets.touch()
        result = self.invoke("1.9.0", "c", success=False)
        self.assertIn("secrets.yaml is empty", result.stderr)
        self.assertEqual(secrets.read_bytes(), b"")
        self.assertFalse(any(c[2:4] == ["gen", "secrets"] for c in self.calls("client")))
        self.assertEqual(self.calls("vimdiff"), [])

    def test_editor_exit_status_is_preserved_and_lock_removed(self):
        self.env["FAKE_EDITOR_EXIT"] = "7"
        result = self.invoke("1.9.0", "c", success=False)
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.cache / ".lock").exists())
        self.assertEqual(list(self.cache.glob(".stage.*")), [])
        self.assertTrue((self.cache / "secrets.yaml").is_file())

    def test_termination_cleans_up_and_retains_cache(self):
        self.env["FAKE_TERMINATE"] = "1"
        result = self.invoke("1.9.0", "c", success=False)
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertIn("STOPPED", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((self.cache / ".lock").exists())
        self.assertEqual(list(self.cache.glob(".stage.*")), [])
        self.assertTrue((self.cache / "secrets.yaml").is_file())
        self.assertTrue((self.cache / "linux-amd64/1.9.0/talosctl").is_file())

    def test_symlink_cache_is_rejected(self):
        destination = self.root / "outside"
        destination.mkdir()
        self.cache.symlink_to(destination, target_is_directory=True)
        self.invoke("1.9.0", "c", success=False)
        self.assertEqual(list(destination.iterdir()), [])

    @unittest.skipUnless(REAL_TALOSCTL, "No local talosctl for offline generation")
    def test_real_cached_client_generates_configs_offline(self):
        result = subprocess.run([REAL_TALOSCTL, "version", "--client", "--short"], capture_output=True, text=True, check=True)
        version = re.search(r"v(\d+\.\d+\.\d+)", result.stdout).group(1)
        self.env.update(FAKE_OS=platform.system(), FAKE_ARCH=platform.machine())
        systems = {"Linux": "linux", "Darwin": "darwin"}
        architectures = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64",
                         "arm64": "arm64", "armv7l": "armv7", "armv7": "armv7"}
        target = systems[platform.system()] + "-" + architectures[platform.machine()]
        version_dir = self.cache / target / version
        version_dir.mkdir(parents=True, mode=0o700)
        binary = version_dir / "talosctl"
        shutil.copy2(REAL_TALOSCTL, binary)
        (version_dir / "talosctl.sha256").write_text(hashlib.sha256(binary.read_bytes()).hexdigest() + "\n")
        self.env["FAKE_DOWNLOAD_FAIL"] = "1"
        self.invoke(version, "c-w", "nc")
        self.assertTrue((version_dir / "controlplane.yaml").is_file())
        self.assertTrue((version_dir / "worker.yaml").is_file())
        self.assertTrue((self.cache / "secrets.yaml").is_file())
        self.assertEqual(self.calls("download"), [])

        # Talos 1.14 emits several YAML documents in each role file. The
        # documentation flag affects comments, not these configuration docs.
        if tuple(map(int, version.split(".")[:2])) == (1, 14):
            def document_kinds(path):
                text = path.read_text()
                self.assertRegex(text, r"(?m)^---$")
                return re.findall(r"^kind: (.+)$", text, re.MULTILINE)

            configs = [version_dir / name for name in ("controlplane.yaml", "worker.yaml")]
            without_comments = [document_kinds(path) for path in configs]
            self.assertIn("KubeAPIServerConfig", without_comments[0])
            self.assertIn("KubeAuthenticationConfig", without_comments[0])
            self.assertIn("KubeletConfig", without_comments[1])
            self.assertEqual([Path(p) for p in self.calls("vimdiff")[-1][1:]], configs)
            self.invoke(version, "c-w")
            self.assertEqual([document_kinds(path) for path in configs], without_comments)
            self.assertEqual([Path(p) for p in self.calls("vimdiff")[-1][1:]], configs)

        # talosctl, not a second YAML parser, rejects a broken existing bundle.
        comparisons = len(self.calls("vimdiff"))
        secrets = self.cache / "secrets.yaml"
        broken = b"cluster: [unterminated\n"
        secrets.write_bytes(broken)
        before = {p: p.read_bytes() for p in version_dir.glob("*.yaml")}
        result = self.invoke(version, "c-w", success=False)
        self.assertIn("Config generation failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(secrets.read_bytes(), broken)
        self.assertEqual({p: p.read_bytes() for p in version_dir.glob("*.yaml")}, before)
        self.assertEqual(len(self.calls("vimdiff")), comparisons)
        self.assertEqual(self.calls("download"), [])


class RedirectTests(unittest.TestCase):
    def test_authorization_is_not_forwarded_to_redirect_targets(self):
        for url in ("https://release-assets.githubusercontent.com/example",
                    "https://api.github.com/other", "https://example.org/file"):
            with self.subTest(url=url):
                request = Request("https://api.github.com/repos/siderolabs/talos/releases/assets/1",
                                  headers={"Authorization": "Bearer synthetic-token",
                                           "Accept": "application/octet-stream"})
                request.add_unredirected_header("Authorization", "Bearer synthetic-token")
                redirected = TOOL.HTTPSRedirectHandler().redirect_request(request, None, 302, "", {}, url)
                self.assertIsNone(redirected.get_header("Authorization"))
                self.assertEqual(redirected.get_header("Accept"), "application/octet-stream")

    def test_redirect_to_plain_http_is_rejected(self):
        request = Request("https://api.github.com/repos/siderolabs/talos/releases/assets/1")
        with self.assertRaisesRegex(TOOL.ToolError, "non-HTTPS"):
            TOOL.HTTPSRedirectHandler().redirect_request(request, None, 302, "", {}, "http://example.org/file")


if __name__ == "__main__":
    unittest.main()
