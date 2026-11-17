#!/usr/bin/env python3
"""Private-source builds; only the standard library is required by this controller."""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "darwin-arm64": ("darwin", "arm64"),
    "darwin-amd64": ("darwin", "amd64"),
    "linux-amd64": ("linux", "amd64"),
    "linux-arm64": ("linux", "arm64"),
    "linux-armv7": ("linux", "arm"),
    "linux-386": ("linux", "386"),
    "android-arm64": ("android", "arm64"),
    "android-armv7": ("android", "arm"),
    "android-amd64": ("android", "amd64"),
    "android-386": ("android", "386"),
}
BREW_TARGETS = ("darwin-arm64", "darwin-amd64", "linux-arm64", "linux-amd64")
NAME = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?"
SHA = r"[0-9a-f]{40}"
MAX_BINARY = 512 * 1024 * 1024


def require(ok, message="configuration rejected"):
    if not ok:
        raise ValueError(message)


def read_json(path):
    require(Path(path).stat().st_size <= 1024 * 1024)
    return json.loads(Path(path).read_text())


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(args, **kwargs):
    return run(args, stdout=subprocess.PIPE, **kwargs).stdout


def output(name, data):
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"{name}={json.dumps(data, separators=(',', ':'))}\n")


def public_project(project):
    require(re.fullmatch(NAME, project) is not None)
    public = read_json(ROOT / "packages" / (project + ".json"))
    require(set(public) == {"schema", "packages", "targets"} and public["schema"] == 1)
    require(public["packages"] and set(public["targets"]) <= TARGETS.keys())
    require(len(public["targets"]) == len(set(public["targets"])))
    for name, package in public["packages"].items():
        require(re.fullmatch(NAME, name) is not None)
        require(set(package) == {"description", "homepage", "license", "test"})
        require(package["license"] == "cannot_represent" or re.fullmatch(r"[A-Za-z0-9.+-]+", package["license"]))
        require(package["homepage"].startswith("https://"))
        require(all(isinstance(x, str) and "\n" not in x for x in package["test"]))
    return public


def safe_path(value):
    require(isinstance(value, str) and value and "\\" not in value and "\0" not in value)
    p = PurePosixPath(value)
    require(not p.is_absolute() and ".." not in p.parts and ".git" not in p.parts)
    return value


def git_ref(value):
    require(isinstance(value, str))
    require(re.fullmatch(SHA, value) or re.fullmatch(r"refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", value))
    require(not any(x in value for x in ("..", "//", "@{", "/.", ".lock")))
    require(not value.endswith(("/", ".")))
    return value


def manifest(config, public):
    require(set(config) == {"schema", "packages"} and config["schema"] == 1)
    require(config["packages"].keys() == public["packages"].keys())
    for name, package in config["packages"].items():
        require(set(package) == {"version", "ref", "go", "command", "modules", "licenses", "notice"})
        require(re.fullmatch(VERSION, package["version"]) is not None)
        git_ref(package["ref"])
        require(re.fullmatch(r"1\.[0-9]+\.[0-9]+", package["go"]) is not None)
        require(isinstance(package["command"], str) and 0 < len(package["command"]) < 16384)
        require(0 < len(package["licenses"]) <= 16)
        for path in package["licenses"]:
            safe_path(path)
        require(isinstance(package["notice"], str) and len(package["notice"]) < 8192)
        require(len(package["modules"]) <= 16)
        for module in package["modules"]:
            require(set(module) == {"module", "ref", "public_module"})
            require(re.fullmatch(r"[A-Za-z0-9._/-]+", module["module"]) is not None)
            require(re.fullmatch(r"tap\.local/[a-z0-9-]+", module["public_module"]) is not None)
            git_ref(module["ref"])
    return config


def source():
    config = json.loads(os.environ["TAP_SOURCE"])
    require(set(config) == {"repo", "ref"})
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config["repo"]) is not None)
    git_ref(config["ref"])
    return config


@contextlib.contextmanager
def repository(work):
    """Never persist an authenticated remote. SSH output is already in the private log."""
    config = source()
    key = work / "key"
    key.write_text(os.environ["TAP_KEY"] + "\n")
    key.chmod(0o600)
    repo = work / "repo"
    run(["git", "init", "--quiet", repo])
    env = {"PATH": os.environ["PATH"], "HOME": str(work), "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_SSH_COMMAND": f"ssh -F /dev/null -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
           f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={ROOT}/.github/known_hosts "
           "-o GlobalKnownHostsFile=/dev/null"}

    def fetch(ref):
        git_ref(ref)
        run(["git", "-C", repo, "fetch", "--quiet", "--no-tags", "--depth=1",
             "git@github.com:" + config["repo"] + ".git", ref], env=env)
        commit = capture(["git", "-C", repo, "rev-parse", "FETCH_HEAD^{commit}"], env=env).decode().strip()
        require(re.fullmatch(SHA, commit) is not None)
        return commit
    try:
        yield repo, fetch, config
    finally:
        key.unlink(missing_ok=True)


def archive(repo, commit, destination):
    destination.mkdir(parents=True)
    raw = capture(["git", "-C", repo, "archive", "--format=tar", commit])
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        # Sources can contain symlinks; only regular files/directories inside the tree are materialized.
        for item in tf.getmembers():
            safe_path(item.name.rstrip("/"))
            require(item.isfile() or item.isdir(), "source links and submodules are not supported")
        tf.extractall(destination, filter="data")


def crypt(input_path, output_path, recipient=None, identity=None):
    require(bool(recipient) != bool(identity))
    args = ["age", "--output", output_path]
    if recipient:
        require(re.fullmatch(r"age1[0-9a-z]{50,100}", recipient) is not None)
        args += ["--recipient", recipient]
        run(args + [input_path])
    else:
        with tempfile.NamedTemporaryFile(mode="w", dir=output_path.parent) as f:
            os.chmod(f.name, 0o600)
            f.write(identity)
            f.flush()
            run(args + ["--decrypt", "--identity", f.name, input_path])


def bundle(path, files):
    """Deterministic, metadata-free flat tar.gz, used for both public and sealed archives."""
    with open(path, "wb") as out, gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as gz:
        with tarfile.open(fileobj=gz, mode="w") as tf:
            for name, data, executable in sorted(files):
                safe_path(name)
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.mtime = len(data), 0o755 if executable else 0o644, 0
                item.uid = item.gid = 0
                tf.addfile(item, io.BytesIO(data))


def unpack(path, limit=MAX_BINARY * 3):
    files = {}
    total = 0
    with tarfile.open(path, "r:gz") as tf:
        for item in tf:
            safe_path(item.name)
            require(item.isfile() and item.name not in files and item.size <= MAX_BINARY)
            total += item.size
            require(total <= limit and len(files) < 100)
            files[item.name] = (tf.extractfile(item).read(), item.mode)
    return files


def plan(args, work):
    public = public_project(args.project)
    with repository(work) as (repo, fetch, config):
        commit = fetch(config["ref"])
        raw = capture(["git", "-C", repo, "show", commit + ":.tap.json"])
        require(len(raw) <= 65536)
        data = manifest(json.loads(raw), public)
        lock = {"schema": 1, "packages": {}}
        for name, package in data["packages"].items():
            package = {**package, "commit": fetch(package["ref"])}
            package["modules"] = [{**m, "commit": fetch(m["ref"])} for m in package["modules"]]
            lock["packages"][name] = package
    versions = {name: p["version"] for name, p in lock["packages"].items()}
    write_json(work / "lock.json", lock)
    args.out.mkdir(parents=True, exist_ok=True)
    crypt(work / "lock.json", args.out / "lock.age", recipient=os.environ["TAP_LOCK_RECIPIENT"])
    # Outputs contain public package names/versions only. Private refs and commands remain sealed.
    output("versions", versions)
    output("targets", public["targets"])
    output("brew", set(BREW_TARGETS) <= set(public["targets"]))


def toolchain(version, work):
    configured = os.environ.get("TAP_GO")
    if configured:
        require(capture([configured, "version"]).decode().split()[2] == "go" + version)
        return Path(configured).resolve().parent
    url = "https://go.dev/dl/?mode=json&include=all"
    with urllib.request.urlopen(url, timeout=90) as r:
        releases = json.load(r)
    item = next(f for r in releases if r["version"] == "go" + version for f in r["files"]
                if f["os"] == "linux" and f["arch"] == "amd64" and f["kind"] == "archive")
    filename = item["filename"]
    require(re.fullmatch(r"go1\.[0-9]+\.[0-9]+\.linux-amd64\.tar\.gz", filename) is not None)
    path = work / filename
    urllib.request.urlretrieve("https://go.dev/dl/" + filename, path)
    require(hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"])
    dest = work / ("toolchain-" + version)
    dest.mkdir()
    with tarfile.open(path) as tf:
        tf.extractall(dest, filter="data")
    return dest / "go/bin"


def sanitize_modules(src, modules):
    """Hide private Go import paths, including symbols and Go build-info, before compiling."""
    replacements = [(m["module"], m["public_module"]) for m in modules]
    mod = (src / "go.mod").read_text()
    own_module = re.search(r"(?m)^module\s+(\S+)", mod).group(1)
    # Public upstream module paths are fine; private main modules are explicitly mapped too.
    for directory in [src] + [Path(m["directory"]) for m in modules]:
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in (".go", ".mod", ".sum"):
                text = path.read_text()
                for before, after in replacements:
                    text = text.replace(before, after)
                    if path.name == "go.mod":
                        # Local replacements do not need the original pseudo-version, which can
                        # otherwise disclose a private commit in Go's embedded build-info.
                        text = re.sub(r"(?m)^(\s*(?:require\s+)?" + re.escape(after) + r")\s+v[^\s)]+",
                                      r"\g<1> v0.0.0", text)
                path.write_text(text)
    with (src / "go.mod").open("a") as f:
        for module in modules:
            if module["module"] != own_module:
                relative = os.path.relpath(module["directory"], src)
                f.write(f'\nreplace {module["public_module"]} => {relative}\n')


def android_cc(arch):
    roots = [os.environ.get(k, "") for k in ("ANDROID_NDK_LATEST_HOME", "ANDROID_NDK_HOME")]
    sdk = Path(os.environ.get("ANDROID_HOME", "/usr/local/lib/android/sdk"))
    roots += [str(p) for p in sorted((sdk / "ndk").glob("*"), reverse=True)]
    triple = {"arm64": "aarch64-linux-android", "arm": "armv7a-linux-androideabi",
              "amd64": "x86_64-linux-android", "386": "i686-linux-android"}[arch]
    for root in roots:
        compiler = Path(root) / "toolchains/llvm/prebuilt/linux-x86_64/bin" / (triple + "24-clang")
        if root and compiler.is_file():
            return str(compiler)
    raise ValueError("Android NDK is not available")


def build(args, work):
    public = public_project(args.project)
    require(args.target in public["targets"])
    crypt(args.lock, work / "lock.json", identity=os.environ["TAP_LOCK_IDENTITY"])
    lock = read_json(work / "lock.json")
    config = {"schema": 1, "packages": {name: {k: v for k, v in p.items() if k != "commit"}
              for name, p in lock["packages"].items()}}
    for p in config["packages"].values():
        p["modules"] = [{k: v for k, v in m.items() if k != "commit"} for m in p["modules"]]
    manifest(config, public)
    # Fetch all immutable snapshots, then delete the key before invoking any source-controlled code.
    sources = {}
    with repository(work) as (repo, fetch, _):
        for i, (name, package) in enumerate(lock["packages"].items()):
            require(fetch(package["commit"]) == package["commit"])
            src = work / f"src{i}"
            archive(repo, package["commit"], src)
            modules = []
            for j, module in enumerate(package["modules"]):
                require(fetch(module["commit"]) == module["commit"])
                dest = work / f"lib{i}-{j}"
                archive(repo, module["commit"], dest)
                modules.append({**module, "directory": str(dest)})
            sources[name] = src, modules
    # No private Git metadata, keys, tokens, runner protocol files, or workflow env reach the build.
    shutil.rmtree(work / "repo")
    bins = {}
    for p in lock["packages"].values():
        if p["go"] not in bins:
            bins[p["go"]] = toolchain(p["go"], work)
    payload = []
    goos, arch = TARGETS[args.target]
    for name, package in lock["packages"].items():
        src, modules = sources[name]
        sanitize_modules(src, modules)
        out = work / "out" / name
        out.mkdir(parents=True)
        home = work / "home" / name
        home.mkdir(parents=True)
        env = {"PATH": str(bins[package["go"]]) + ":/usr/bin:/bin", "HOME": str(home),
               "TMPDIR": str(work / "tmp"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "GOOS": goos, "GOARCH": arch, "GOARM": "7", "GOAMD64": "v1", "GOTOOLCHAIN": "local",
               "CGO_ENABLED": "0", "GOCACHE": str(work / "cache"),
               "GOMODCACHE": str(work / "modcache"), "GOPATH": str(work / "gopath"),
               "GOFLAGS": "-mod=readonly -trimpath -buildvcs=false", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
               "TAP_OUT": str(out), "VERSION": package["version"],
               "SOURCE_DATE_EPOCH": "0", "TZ": "UTC"}
        if goos == "linux" and arch == "386":
            env["GO386"] = "softfloat"
        if goos == "android":
            env.update(CGO_ENABLED="1", CC=android_cc(arch),
                       CGO_CFLAGS="-O2 -g0 -ffile-prefix-map=/tmp=/build",
                       CGO_LDFLAGS="-Wl,--build-id=none")
        (work / "tmp").mkdir(exist_ok=True)
        run(["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", package["command"]],
            cwd=src, env=env, stdin=subprocess.DEVNULL, timeout=5400)
        binary = out / name
        require(binary.is_file() and not binary.is_symlink())
        files = [(name, binary.read_bytes(), True), ("NOTICE.txt", package["notice"].encode(), False)]
        for i, license_path in enumerate(package["licenses"]):
            file = (src / safe_path(license_path)).resolve()
            require(file.is_relative_to(src) and file.is_file() and file.stat().st_size < 1024 * 1024)
            files.append((f"LICENSES/{i}.txt", file.read_bytes(), False))
        # Include private dependency license notices without including dependency source code.
        index = len(package["licenses"])
        for module in modules:
            for file in sorted(Path(module["directory"]).glob("LICENSE*")):
                if file.is_file():
                    files.append((f"LICENSES/{index}.txt", file.read_bytes(), False))
                    index += 1
        filename = asset_name(name, package["version"], args.target)
        bundle(work / filename, files)
        payload.append((filename, (work / filename).read_bytes(), False))
    bundle(work / "payload.tar.gz", payload)
    args.out.mkdir(parents=True, exist_ok=True)
    crypt(work / "payload.tar.gz", args.out / "payload.age", recipient=os.environ["TAP_PAYLOAD_RECIPIENT"])


def asset_name(name, version, target):
    require(re.fullmatch(NAME, name) and re.fullmatch(VERSION, version) and target in TARGETS)
    return f"{name}-{version}-{target}.tar.gz"


def binary_target(data, target):
    goos, arch = TARGETS[target]
    if goos == "darwin":
        require(len(data) >= 32 and data[:4] == b"\xcf\xfa\xed\xfe", "Mach-O required")
        cpu, _, kind = struct.unpack_from("<III", data, 4)
        require(cpu == {"arm64": 0x100000c, "amd64": 0x1000007}[arch] and kind == 2)
        # Go's arm64 linker embeds an ad-hoc signature; installation signs the final file again.
        return
    require(len(data) >= 64 and data[:4] == b"\x7fELF" and data[5] == 1, "ELF required")
    bits = {"arm64": 2, "amd64": 2, "arm": 1, "386": 1}[arch]
    require(data[4] == bits)
    kind, machine = struct.unpack_from("<HH", data, 16)
    require(machine == {"arm64": 183, "amd64": 62, "arm": 40, "386": 3}[arch])
    require(kind in (2, 3))
    if bits == 2:
        offset = struct.unpack_from("<Q", data, 32)[0]
        size, count = struct.unpack_from("<HH", data, 54)
    else:
        offset = struct.unpack_from("<I", data, 28)[0]
        size, count = struct.unpack_from("<HH", data, 42)
    require(size >= (56 if bits == 2 else 32) and count <= 1024 and offset + size * count <= len(data))
    interpreters = []
    for i in range(count):
        pos = offset + i * size
        ptype = struct.unpack_from("<I", data, pos)[0]
        if ptype == 3:
            if bits == 2:
                start, length = struct.unpack_from("<Q", data, pos + 8)[0], struct.unpack_from("<Q", data, pos + 32)[0]
            else:
                start, length = struct.unpack_from("<I", data, pos + 4)[0], struct.unpack_from("<I", data, pos + 16)[0]
            require(start + length <= len(data))
            interpreters.append(data[start:start + length].rstrip(b"\0"))
    if goos == "linux":
        require(not interpreters, "Linux binaries must be static for glibc/musl portability")
    else:
        expected = b"/system/bin/linker64" if bits == 2 else b"/system/bin/linker"
        require(kind == 3 and interpreters == [expected], "Android PIE with Bionic linker required")


def leak_scan(data, denylist):
    lower = data.lower()
    for term in denylist:
        require(term.encode().lower() not in lower, "publication gate rejected private marker")
    # Crypto-aware programs legitimately contain PEM *header strings*. Require a full
    # encoded key block so those parser constants do not prevent rclone publication.
    for pattern in (rb"-----BEGIN ((?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----\r?\n"
                    rb"(?:Proc-Type: [^\r\n]+\r?\nDEK-Info: [^\r\n]+\r?\n\r?\n)?"
                    rb"[A-Za-z0-9+/=\r\n]{64,}-----END \1-----",
                    rb"AGE-SECRET-KEY-1[0-9A-Z]{58}",
                    rb"gh[pousr]_[A-Za-z0-9]{30,}", rb"github_pat_[A-Za-z0-9_]{40,}",
                    rb"https://[^\s/]+:[^\s/@]+@github\.com",
                    rb"/tmp/tap-private-[A-Za-z0-9_-]+/"):
        require(re.search(pattern, data) is None, "publication gate rejected sensitive material")


def gate(args, work):
    public = public_project(args.project)
    versions = json.loads(os.environ["TAP_VERSIONS"])
    require(versions.keys() == public["packages"].keys())
    require(all(re.fullmatch(VERSION, v) for v in versions.values()))
    denylist = [x.strip() for x in os.environ.get("TAP_DENYLIST", "").splitlines() if x.strip()]
    require(denylist, "TAP_DENYLIST must contain private publication markers")
    expected = {asset_name(name, v, t) for name, v in versions.items() for t in public["targets"]}
    found = {}
    args.out.mkdir(parents=True, exist_ok=True)
    for target in public["targets"]:
        crypt(args.payloads / ("payload-" + target) / "payload.age", work / (target + ".tar.gz"),
              identity=os.environ["TAP_PAYLOAD_IDENTITY"])
        archives = unpack(work / (target + ".tar.gz"))
        require(set(archives) == {asset_name(n, v, target) for n, v in versions.items()})
        for name, version in versions.items():
            filename = asset_name(name, version, target)
            raw, _ = archives[filename]
            (work / filename).write_bytes(raw)
            files = unpack(work / filename, MAX_BINARY + 16 * 1024 * 1024)
            require(name in files and "NOTICE.txt" in files and any(p.startswith("LICENSES/") for p in files))
            for path, (data, mode) in files.items():
                require(path in (name, "NOTICE.txt") or re.fullmatch(r"LICENSES/[0-9]+\.txt", path))
                require(mode == (0o755 if path == name else 0o644))
                if path != name:
                    require(len(data) <= 1024 * 1024)
                    data.decode("utf-8")
                leak_scan(data, denylist)
            binary_target(files[name][0], target)
            # Repack on this clean runner; no source-controlled tar metadata is trusted.
            bundle(args.out / filename, [(p, data, p == name) for p, (data, _) in files.items()])
            found[filename] = hashlib.sha256((args.out / filename).read_bytes()).hexdigest()
    require(set(found) == expected)
    write_json(args.out / "checksums.json", found)
    write_json(args.out / "versions.json", versions)
    (args.out / "SHA256SUMS").write_text("".join(f"{h}  {f}\n" for f, h in sorted(found.items())))


def ruby_string(value):
    # Ruby interpolation must be escaped even though JSON already quotes strings.
    return json.dumps(value, ensure_ascii=False).replace("#{", r"\#{").replace("#@", r"\#@").replace("#$", r"\#$")


def formula(name, version, meta, hashes, targets, repository_name):
    require(all(t in targets for t in BREW_TARGETS), "Homebrew needs all four desktop archives")
    klass = "".join(p.capitalize() for p in name.split("-"))
    lines = [f"class {klass} < Formula", f"  desc {ruby_string(meta['description'])}",
             f"  homepage {ruby_string(meta['homepage'])}", f"  version {ruby_string(version)}",
             "  license :cannot_represent" if meta["license"] == "cannot_represent" else f"  license {ruby_string(meta['license'])}", ""]
    tag = f"{name}-v{version}"
    for system, prefix in (("macos", "darwin"), ("linux", "linux")):
        lines.append(f"  on_{system} do")
        for cpu, arch in (("arm", "arm64"), ("intel", "amd64")):
            asset = asset_name(name, version, prefix + "-" + arch)
            lines += [f"    on_{cpu} do", f'      url "https://github.com/{repository_name}/releases/download/{tag}/{asset}"',
                      f'      sha256 "{hashes[asset]}"', "    end"]
        lines += ["  end", ""]
    lines += ["  def install", f"    bin.install {ruby_string(name)}", '    pkgshare.install "LICENSES", "NOTICE.txt"',
              "  end", "", "  def post_install", "    return unless OS.mac?", "",
              f"    executable = bin/{ruby_string(name)}",
              '    attributes = Utils.safe_popen_read("/usr/bin/xattr", executable).lines.map(&:strip)',
              '    if attributes.include?("com.apple.quarantine")',
              '      system "/usr/bin/xattr", "-d", "com.apple.quarantine", executable', "    end",
              '    system "/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", executable',
              '    system "/usr/bin/codesign", "--verify", "--strict", executable', "  end", "", "  test do"]
    lines += [f'    assert_match version.to_s, shell_output("#{{bin}}/{name} {" ".join(meta["test"])}")', "  end", "end", ""]
    return "\n".join(lines)


def gh_json(args, data=None):
    command = ["gh", "api"] + args
    if data is not None:
        command += ["--input", "-"]
    return json.loads(capture(command, input=json.dumps(data).encode() if data is not None else None))


def publish(args, work):
    public = public_project(args.project)
    versions = read_json(args.out / "versions.json")
    hashes = read_json(args.out / "checksums.json")
    repo = os.environ["GITHUB_REPOSITORY"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/homebrew-[A-Za-z0-9_.-]+", repo))
    require(versions == json.loads(os.environ["TAP_VERSIONS"]))
    formulae = ROOT / "Formula"
    formulae.mkdir(exist_ok=True)
    # Release assets are immutable after publication; failed drafts are safely resumable.
    for name, version in versions.items():
        tag = f"{name}-v{version}"
        assets = [asset_name(name, version, t) for t in public["targets"]]
        checksum = work / "SHA256SUMS"
        checksum.write_text("".join(f"{hashes[a]}  {a}\n" for a in sorted(assets)))
        probe = subprocess.run(["gh", "release", "view", tag, "-R", repo, "--json", "isDraft"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if probe.returncode == 0 and not json.loads(probe.stdout)["isDraft"]:
            download = work / name
            download.mkdir()
            run(["gh", "release", "download", tag, "-R", repo, "--dir", download])
            require({p.name for p in download.iterdir()} == set(assets) | {"SHA256SUMS"})
            require((download / "SHA256SUMS").read_bytes() == checksum.read_bytes(), "published version is immutable")
            require(all(hashlib.sha256((download / a).read_bytes()).hexdigest() == hashes[a] for a in assets))
        else:
            require(probe.returncode in (0, 1))
            notes = work / "notes.txt"
            notes.write_text(f"{name} {version}\n\nHomebrew: `brew install xuniv/tap/{name}`\n\n"
                             "Other terminals: download the matching archive and verify SHA256SUMS. "
                             "Linux archives are static (glibc or musl); ARMv7 uses hard-float. "
                             "Android archives use Bionic, PIE and API 24 (Android 7+) for Termux. "
                             "For iSH on iOS use linux-386. Native iOS/a-Shell are not supported.\n\n"
                             "The modified fork and dependency license notices are included. "
                             "macOS binaries use local ad-hoc signing, without Apple notarization.\n")
            if probe.returncode != 0:
                run(["gh", "release", "create", tag, "-R", repo, "--draft", "--title", f"{name} {version}",
                     "--notes-file", notes, "--target", "main"])
            run(["gh", "release", "upload", tag, "-R", repo, "--clobber", checksum] + [args.out / a for a in assets])
            # Verify the bytes returned by GitHub before making a draft public.
            verify = work / (name + "-verify")
            verify.mkdir()
            run(["gh", "release", "download", tag, "-R", repo, "--dir", verify])
            require({p.name for p in verify.iterdir()} == set(assets) | {"SHA256SUMS"})
            require(all(hashlib.sha256((verify / a).read_bytes()).hexdigest() == hashes[a] for a in assets))
            run(["gh", "release", "edit", tag, "-R", repo, "--draft=false", "--latest=false", "--notes-file", notes])
        if set(BREW_TARGETS) <= set(public["targets"]):
            (formulae / (name + ".rb")).write_text(formula(name, version, public["packages"][name], hashes, public["targets"], repo))
    run(["git", "-C", ROOT, "config", "user.name", "github-actions[bot]"])
    run(["git", "-C", ROOT, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "-C", ROOT, "add", "Formula"])
    if subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"]).returncode:
        run(["git", "-C", ROOT, "commit", "-m", "Update formulae for verified binary releases"])
        # Checkout has no persisted credentials. Do not put this token into a remote URL or Git config.
        env = dict(os.environ)
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="!gh auth git-credential")
        run(["git", "-C", ROOT, "push", "origin", "HEAD:main"], env=env)


def poll(args, work):
    # The resolver emits only allowlisted public versions. This job never executes source code.
    plan(args, work)
    lock = read_json(work / "lock.json")
    repo = os.environ["GITHUB_REPOSITORY"]
    missing = False
    for name, p in lock["packages"].items():
        tag = f"{name}-v{p['version']}"
        probe = subprocess.run(["gh", "release", "view", tag, "-R", repo, "--json", "isDraft"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if probe.returncode != 0 or json.loads(probe.stdout)["isDraft"]:
            missing = True
    # A green publication needs both a release and a formula. Retry a failed formula commit too.
    for name, p in lock["packages"].items():
        f = ROOT / "Formula" / (name + ".rb")
        if set(BREW_TARGETS) <= set(public_project(args.project)["targets"]):
            missing |= not f.exists() or f'  version "{p["version"]}"' not in f.read_text()
    if missing:
        run(["gh", "workflow", "run", "build.yml", "-R", repo, "--ref", "main", "-f", "project=" + args.project])


def private_main(args):
    """Redirect OS file descriptors: compiler output and traceback never reach Actions logs."""
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    diagdir = args.out / "diag"
    diagdir.mkdir(exist_ok=True)
    status = 0
    with tempfile.TemporaryDirectory(prefix="tap-private-") as temp:
        work = Path(temp)
        log = work / "diagnostics.log"
        saved = os.dup(1), os.dup(2)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            with log.open("wb") as f:
                os.dup2(f.fileno(), 1)
                os.dup2(f.fileno(), 2)
                try:
                    globals()[args.command](args, work)
                except BaseException:
                    import traceback
                    traceback.print_exc()
                    status = 1
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
        # No plaintext diagnostics artifact is ever uploaded, even when age/configuration fails.
        if os.environ.get("TAP_LOG_RECIPIENT"):
            try:
                bundle(work / "diag.tar.gz", [("diagnostics.log", log.read_bytes(), False)])
                crypt(work / "diag.tar.gz", diagdir / (args.command + ".age"), recipient=os.environ["TAP_LOG_RECIPIENT"])
            except Exception:
                print("::warning::encrypted diagnostics unavailable")
    print("operation completed" if not status else "::error::operation failed; inspect encrypted diagnostics")
    return status


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("plan", "build", "gate", "publish", "poll", "validate"))
    p.add_argument("--project", default="tools")
    p.add_argument("--out", type=Path, default=Path("dist"))
    p.add_argument("--target", choices=TARGETS)
    p.add_argument("--lock", type=Path)
    p.add_argument("--payloads", type=Path)
    args = p.parse_args()
    if args.command == "validate":
        for path in (ROOT / "packages").glob("*.json"):
            public_project(path.stem)
        return 0
    return private_main(args)


if __name__ == "__main__":
    sys.exit(main())
