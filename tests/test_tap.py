import io
import gzip
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tap


class GateTests(unittest.TestCase):
    def test_archive_rejects_traversal_links_duplicates_and_oversize(self):
        for case in ("../outside", "link", "duplicate", "oversize"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "bad.tar.gz"
                if case == "oversize":
                    item = tarfile.TarInfo("file")
                    item.size = tap.MAX_BINARY + 1
                    path.write_bytes(gzip.compress(item.tobuf() + b"\0" * 1024))
                    with self.assertRaises(ValueError):
                        tap.unpack(path)
                    continue
                with tarfile.open(path, "w:gz") as archive:
                    item = tarfile.TarInfo("../outside" if case == "../outside" else "file")
                    if case == "link":
                        item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    archive.addfile(item)
                    if case == "duplicate":
                        archive.addfile(item)
                with self.assertRaises(ValueError):
                    tap.unpack(path)

    def elf(self, arch="arm64", interpreter=None, android=False):
        bits = 1 if arch in ("arm", "386") else 2
        data = bytearray(256)
        data[:6] = b"\x7fELF" + bytes((bits, 1))
        struct.pack_into("<HH", data, 16, 3 if android else 2,
                         {"arm64": 183, "amd64": 62, "arm": 40, "386": 3}[arch])
        if bits == 2:
            struct.pack_into("<Q", data, 32, 64)
            struct.pack_into("<HH", data, 54, 56, 1)
        else:
            struct.pack_into("<I", data, 28, 64)
            struct.pack_into("<HH", data, 42, 32, 1)
        if interpreter:
            struct.pack_into("<I", data, 64, 3)
            if bits == 2:
                struct.pack_into("<Q", data, 72, 160)
                struct.pack_into("<Q", data, 96, len(interpreter) + 1)
            else:
                struct.pack_into("<I", data, 68, 160)
                struct.pack_into("<I", data, 80, len(interpreter) + 1)
            data[160:160 + len(interpreter)] = interpreter
        return data

    def test_linux_requires_correct_architecture_and_static_linking(self):
        tap.binary_target(self.elf(), "linux-arm64")
        for data in (self.elf("amd64"), self.elf(interpreter=b"/lib/ld-linux-aarch64.so.1")):
            with self.assertRaises(ValueError):
                tap.binary_target(data, "linux-arm64")
        tap.binary_target(self.elf("arm"), "linux-armv7")
        tap.binary_target(self.elf("386"), "linux-386")

    def test_android_requires_bionic_and_pie(self):
        tap.binary_target(self.elf(android=True, interpreter=b"/system/bin/linker64"), "android-arm64")
        for data in (self.elf(), self.elf(android=True, interpreter=b"/lib/ld-linux-aarch64.so.1")):
            with self.assertRaises(ValueError):
                tap.binary_target(data, "android-arm64")

    def test_macho_cpu_and_file_type(self):
        data = b"\xcf\xfa\xed\xfe" + struct.pack("<III", 0x100000c, 0, 2) + b"\0" * 32
        tap.binary_target(data, "darwin-arm64")
        with self.assertRaises(ValueError):
            tap.binary_target(data, "darwin-amd64")

    def test_gate_rejects_secrets_and_private_markers(self):
        for data in (b"github.com/private/source", b"ghp_" + b"A" * 36,
                     b"-----BEGIN OPENSSH PRIVATE KEY-----", b"/tmp/tap-private-123/src0/main.go"):
            with self.assertRaises(ValueError):
                tap.leak_scan(data, ["github.com/private/source"])
        tap.leak_scan(b"Copyright original upstream authors", ["github.com/private/source"])

    def test_repacking_removes_author_and_timestamp_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.tar.gz", Path(d) / "b.tar.gz"
            tap.bundle(a, [("binary", b"ok", True)])
            tap.bundle(b, [("binary", b"ok", True)])
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with tarfile.open(a) as archive:
                member = archive.next()
                self.assertEqual((member.uid, member.gid, member.uname, member.gname, member.mtime), (0, 0, "", "", 0))

    def test_private_errors_do_not_reach_actions_logs(self):
        code = '''import sys, argparse
from pathlib import Path
sys.path.insert(0, "tools")
import tap
def fail(args, work):
    print("PRIVATE_SOURCE_MARKER", flush=True)
    raise RuntimeError("PRIVATE_SOURCE_MARKER")
tap.plan = fail
sys.exit(tap.private_main(argparse.Namespace(command="plan", out=Path(sys.argv[1]))))
'''
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ)
            env.pop("TAP_LOG_RECIPIENT", None)
            result = subprocess.run([sys.executable, "-c", code, d], cwd=tap.ROOT, env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn(b"PRIVATE_SOURCE_MARKER", result.stdout + result.stderr)
            self.assertNotIn(b"RuntimeError", result.stdout + result.stderr)

    def test_private_module_names_removed_from_sources_and_go_build_info(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src, lib = root / "src", root / "lib"
            src.mkdir()
            lib.mkdir()
            (src / "go.mod").write_text("module upstream/public\nrequire private.example/secret v0.0.0-20260101000000-123456abcdef\n")
            (src / "main.go").write_text('package main\nimport "private.example/secret/api"\n')
            (lib / "go.mod").write_text("module private.example/secret\n")
            tap.sanitize_modules(src, [{"module": "private.example/secret", "public_module": "tap.local/lib", "directory": str(lib)}])
            self.assertNotIn("private.example", (src / "go.mod").read_text())
            self.assertNotIn("123456abcdef", (src / "go.mod").read_text())
            self.assertIn("require tap.local/lib v0.0.0", (src / "go.mod").read_text())
            self.assertIn("tap.local/lib/api", (src / "main.go").read_text())

    def test_full_gate_requires_every_expected_asset_and_checks_inner_files(self):
        import argparse
        import shutil
        public = {"schema": 1, "packages": {"example": {}}, "targets": ["linux-arm64"]}
        for scenario in ("valid", "missing", "private-marker", "wrong-cpu"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                work, inputs, out = root / "work", root / "inputs", root / "out"
                work.mkdir()
                directory = inputs / "payload-linux-arm64"
                directory.mkdir(parents=True)
                binary = bytes(self.elf("amd64" if scenario == "wrong-cpu" else "arm64"))
                notice = b"private.example/source" if scenario == "private-marker" else b"modified software"
                inner = root / "inner.tar.gz"
                tap.bundle(inner, [("example", binary, True), ("NOTICE.txt", notice, False), ("LICENSES/0.txt", b"License", False)])
                name = "example-1.2.3-linux-arm64.tar.gz"
                tap.bundle(directory / "payload.age", [] if scenario == "missing" else [(name, inner.read_bytes(), False)])
                args = argparse.Namespace(project="project", payloads=inputs, out=out)
                env = {"TAP_VERSIONS": '{"example":"1.2.3"}', "TAP_DENYLIST": "private.example/source", "TAP_PAYLOAD_IDENTITY": "test"}
                def decrypt(source, destination, **kwargs):
                    shutil.copyfile(source, destination)
                with patch.object(tap, "public_project", return_value=public), patch.object(tap, "crypt", decrypt), patch.dict(os.environ, env):
                    if scenario == "valid":
                        tap.gate(args, work)
                        self.assertTrue((out / name).exists())
                        self.assertEqual(tap.read_json(out / "versions.json"), {"example": "1.2.3"})
                    else:
                        with self.assertRaises(ValueError):
                            tap.gate(args, work)

    def test_formula_routes_all_supported_brew_cpus_and_limits_mac_changes(self):
        meta = tap.public_project("tools")["packages"]["degoo"]
        hashes = {tap.asset_name("degoo", "0.1.0", t): "a" * 64 for t in tap.BREW_TARGETS}
        text = tap.formula("degoo", "0.1.0", meta, hashes, tap.BREW_TARGETS, "xuniv/homebrew-tap")
        self.assertEqual(text.count("sha256"), 4)
        self.assertIn("return unless OS.mac?", text)
        self.assertIn('"--timestamp=none"', text)
        self.assertNotIn('"-r"', text)
        self.assertNotIn("spctl", text)
        self.assertNotIn("android", text)

    def test_refs_and_paths_reject_injection(self):
        for ref in ("master", "refs/heads/a..b", "refs/heads/a.lock", "refs/heads/$(env)"):
            with self.assertRaises(ValueError):
                tap.git_ref(ref)
        for path in ("../x", "/tmp/x", ".git/config", "a/../../b"):
            with self.assertRaises(ValueError):
                tap.safe_path(path)


if __name__ == "__main__":
    unittest.main()
