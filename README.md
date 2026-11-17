# xuniv Homebrew tap

Public binary releases built from registered repositories containing a root `.tap.json`.
Private source names, refs, commands and diagnostics stay out of public Actions logs.

After the first successful publication:

```sh
brew install xuniv/tap/dclone
brew install xuniv/tap/degoo
```

`dclone` is the rclone fork with the Degoo backend; it installs a `dclone` executable.
`degoo` is the standalone CLI. Each package has its own immutable versioned release and checksums.
License and modification notices ship with every archive and are installed under Homebrew's share directory.

## Other terminals

Download the matching `.tar.gz` and `SHA256SUMS` from [Releases](https://github.com/xuniv/homebrew-tap/releases).
Check the SHA-256 before extracting, then put the executable in your terminal's `PATH`.

| Archive target | Intended environment |
| --- | --- |
| `darwin-arm64` | Apple Silicon macOS, Homebrew |
| `darwin-amd64` | Intel macOS (Homebrew Tier 3), direct download |
| `linux-arm64` | 64-bit ARM / AArch64 Linux, glibc or musl; Homebrew on supported glibc systems |
| `linux-amd64` | x86_64 Linux, glibc or musl; Homebrew on supported glibc systems |
| `linux-armv7` | 32-bit ARMv7 Linux, static hard-float binary; direct download |
| `linux-386` | 32-bit x86 Linux with soft-float for iSH's Alpine userspace on iOS; direct download |
| `android-arm64` | 64-bit ARM Termux, Android 7/API 24 or newer |
| `android-armv7` | 32-bit ARM Termux, Android 7/API 24 or newer |
| `android-amd64` | x86_64 Android / Termux and Android emulators, API 24 or newer |
| `android-386` | 32-bit x86 Android / Termux and Android emulators, API 24 or newer |

`arm64` and `aarch64` name the same CPU architecture. The OS/ABI still matters: use **Android**
archives for Termux (Bionic), and **Linux** archives for Linux/proot/iSH (static Go, glibc or musl).
Homebrew does not officially support musl, Android, ARMv7, 32-bit x86 or iOS. Those downloads are
independent of the tap. Native iOS, a-Shell/WASI and arbitrary iOS terminal apps are not supported.
Cross-compilation and structural validation do not replace device testing on these terminals.
The static Linux and pure-Go macOS builds omit CGO-only features such as some FUSE mount backends.

The Homebrew formula uses the current `on_macos` / `on_linux` and `on_arm` / `on_intel` DSL,
architecture-specific SHA-256 downloads and `post_install`. On macOS it removes quarantine only
from its installed executable and applies and verifies an ad-hoc signature. No notarization or
Developer ID certificate is supplied. Direct macOS downloads need the same local preparation:

```sh
# Run in the directory containing the extracted executable; substitute degoo if needed.
xattr -d com.apple.quarantine ./dclone 2>/dev/null || true
codesign --force --sign - --timestamp=none ./dclone
```

Current Homebrew accepts the fully qualified `brew install xuniv/tap/NAME` as trust for that
specific formula. For tap-wide inspection commands such as `brew readall`, explicitly trust the
tap with `brew trust --tap xuniv/tap`. CI does this on its disposable runners and rejects a
validation log that says formulae were skipped as untrusted.

## One-time owner setup

The owner-side command requires Python 3.12+, Git, `gh`, `ssh-keygen`, `age` and `age-keygen`.
Use a personal `gh` login with repository administration, Actions secrets/variables, environments
and workflow access. A code-only GitHub App token cannot provision deploy keys or Actions secrets.

```sh
git clone https://github.com/xuniv/homebrew-tap.git
cd homebrew-tap
# On macOS: brew install gh age
bin/tap bootstrap --source xuniv/degoo --project tools --dispatch
```

The setup reads `.tap.json`, registers a read-only source deploy key, restricts the CI environments
to `main`, and provisions the encryption keys. Keep `~/.config/xuniv-tap` private and backed up.
The diagnostics decryption identity stays on your computer; CI only gets its public recipient.
Use `--denylist-file PATH` to add personal terms to the publication gate.

New sources use another public alias and another deploy key:

```sh
bin/tap discover
bin/tap bootstrap --source OWNER/PRIVATE_REPO --project PUBLIC_ALIAS
# Review and commit packages/PUBLIC_ALIAS.json, then dispatch build.yml for PUBLIC_ALIAS.
```

Discovery runs in the owner's local session and records candidate source names only in the private
local state directory. CI polls **registered** manifests every six hours; adding `.tap.json` alone
does not grant a public workflow access to an unregistered private repository. There is no
account-wide private-source PAT in public CI.

See [the manifest contract](docs/manifest.md) and [operations and privacy](docs/operations.md).

## Development

```sh
python3 tools/tap.py validate
python3 -m unittest discover -s tests -v
```

The publisher uses Python 3.12+. Go toolchains are selected by the private manifest and verified
against the official Go download checksums. GitHub's Ubuntu runner supplies the Android NDK.
Prebuilt archives use the formula download DSL; they are not mislabelled as official Homebrew bottles.
