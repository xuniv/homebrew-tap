# Manifest contract (schema 1)

A registered source's default branch contains `.tap.json`. The private `TAP_SOURCE` environment
secret selects that branch. The resolver reads and validates it without checking out or executing
source, resolves package and dependency refs to immutable commits, and encrypts that build lock.
All target jobs use those exact snapshots even if a branch moves during the run.

```json
{
  "schema": 1,
  "packages": {
    "example": {
      "version": "1.2.3",
      "ref": "refs/heads/main",
      "go": "1.27.4",
      "command": "go build -ldflags '-s -w -buildid=' -o \"$TAP_OUT/example\" ./cmd/example",
      "modules": [],
      "licenses": ["LICENSE"],
      "notice": "This distribution includes modifications to the original software."
    }
  }
}
```

The public registry `packages/ALIAS.json` declares public package names, descriptions, homepages,
license classification, version-test arguments and the target list. Package names must agree
exactly with the private manifest. Public registry files contain no source names or source refs.
Different projects should use distinct package names.

- `version`: three or four numeric components. Bump this to publish changed bytes; existing public
  versions cannot be overwritten. A version may be shared with upstream or use a fork revision.
- `ref`: full `refs/heads/...`, `refs/tags/...`, or a 40-character commit SHA.
- `go`: exact stable Go version, downloaded with SHA-256 verification. The current builder runs on
  Linux x86_64; pure Go cross-compiles to macOS and Linux. Android uses the NDK compiler, API 24.
- `command`: Bash with `-eo pipefail`. It must write exactly `$TAP_OUT/PACKAGE_NAME`.
  It receives `VERSION`, `GOOS`, `GOARCH`, `GOARM=7`, an isolated HOME/cache/temp directory,
  the selected toolchain and `CGO_ENABLED=0` (Linux/macOS) or `1` (Android). `GOFLAGS` sets
  `-mod=readonly -trimpath -buildvcs=false`. Keep `-s -w -buildid=` in linker flags too.
  The runner's credentials, source deploy key and Actions protocol file paths are not passed in.
- `modules`: same-repository Go dependencies to materialize before deleting the deploy key.
  Each object has `module`, `public_module` (under `tap.local/`) and `ref`. The builder rewrites
  private imports/module declarations and uses a local replacement, so no private module fetch
  credentials are needed during compilation and private paths do not remain in Go build-info.
  Pin dependency refs to the commit required by the package's `go.mod`; do not silently substitute
  the newest library. A private main module can map itself through an entry as well.
- `licenses`: source-relative license files. The builder includes these and top-level `LICENSE*`
  notices from materialized modules as `LICENSES/N.txt`. Licensing is preserved independently of
  source anonymity. `notice` becomes `NOTICE.txt`.

Only regular source files/directories are accepted; symlinks, submodules and source archives with
path traversal are rejected. This intentionally narrow first implementation supports Go CLI
projects. Other build systems need an explicit toolchain adapter, rather than silently obtaining
credentials or arbitrary system packages from the private build command.

The provided project builds rclone under the output name `dclone`, stamps its fork version, and
links the exact pinned Degoo library from the same private repository. The standalone `degoo`
CLI is built from the default branch and stamps its own version independently.
