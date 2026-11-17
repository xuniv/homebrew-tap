# Publication and privacy

The pipeline follows the separation used by `xuniv/alt`:

1. **Resolve:** an environment scoped to a public alias reads a single private source using a
   read-only SSH deploy key and pinned GitHub host keys. Private config and immutable commits
   are put in an age-encrypted lock artifact. Public outputs contain only package versions and targets.
2. **Build:** target jobs fetch the exact commits, snapshot into generic paths and remove the key
   and Git metadata. Private Go modules are materialized and renamed before source commands run.
   Source stdout/stderr and tracebacks are redirected at the OS file-descriptor level to a local file.
   Builds run with an allowlisted environment. Only age-encrypted payloads and diagnostics are uploaded.
3. **Gate:** a fresh runner, with no source checkout or source code execution, decrypts the payloads.
   It checks exact archive names, normalizes tar/gzip metadata, rejects links/traversal/duplicates,
   checks executable type and CPU, enforces static Linux or Android PIE/Bionic linking and scans
   every executable/license/notice for configured private markers and credential patterns.
4. **Publish:** only a passing gate creates draft releases, uploads and downloads assets to check
   SHA-256, publishes them, then commits the matching Homebrew formulae. Existing public releases
   are immutable. If release publishing succeeds but the formula commit fails, rerunning repairs
   the formula only when rebuilt bytes still match the published checksums.
5. **Test and clean:** macOS ARM64 and Linux ARM64/x86_64 runners install and test formulae.
   Encrypted transfer artifacts are removed even on failure. Encrypted diagnostics expire in three days.

The source-build environment never receives the publication identity, denylist or a write token.
The publication environment never executes the supplied binaries. Neither raw source nor plaintext
build diagnostics are stored as Actions artifacts. All actions are pinned to commit SHAs.
Public workflows take a public alias only: do not dispatch them with private refs in inputs, run
titles, matrix values, notes or filenames.

This protects against accidental log/metadata disclosure. Source and dependencies are trusted
code: clearing environment variables is not an adversarial sandbox. A malicious build runs as
the runner user, has network access and could attack the runner's other channels. Review source
and lock changes, keep publication secrets on the separate gate runner, and do not run untrusted
fork pull requests with source environments. Public Go binaries still include public dependency
names and runtime strings; the gate is a denylist/credential scan, not proof of semantic anonymity.
The operator supplies personal privacy terms; legal copyright notices are intentionally retained.

## Environments and keys

| Location | Value | Purpose |
| --- | --- | --- |
| `tap-ALIAS` secret | `TAP_SOURCE` | Private repository and manifest ref JSON |
| `tap-ALIAS` secret | `TAP_KEY` | Read-only source deploy key |
| `tap-ALIAS` secret | `TAP_LOCK_IDENTITY` | Decrypt only this project's build lock |
| `tap-ALIAS` variable | `TAP_LOCK_RECIPIENT` | Encrypt this project's build lock |
| Repo variable | `TAP_PAYLOAD_RECIPIENT` | Encrypt payloads for the gate |
| Repo variable | `TAP_LOG_RECIPIENT` | Encrypt diagnostics for the owner |
| `tap-publish` secret | `TAP_PAYLOAD_IDENTITY` | Decrypt build payloads on the gate runner |
| `tap-publish` secret | `TAP_DENYLIST` | Required newline-separated private markers |
| Owner's local state | `log-identity.txt` | Diagnostics decryption; never uploaded to CI |

Both environments restrict deployments/access to `main`. `deployment: false` avoids creating
public deployment records for private-source operations. Never expose source keys to PR jobs.
Keep local setup state backed up: replacing a lost log identity makes previous diagnostics unreadable.

## Diagnose a failure

```sh
gh run list -R xuniv/homebrew-tap --workflow build.yml
gh run download RUN_ID -R xuniv/homebrew-tap --name diag-linux-arm64 --dir /tmp/tap-diag
age -d -i ~/.config/xuniv-tap/log-identity.txt /tmp/tap-diag/build.age | tar -xzO
```

Diagnostics contain private commands, paths and potentially source excerpts. Read them locally.
Do not paste them into public issues or rerun with shell tracing or Actions debug logging enabled.

## Publish an update

Commit source changes and bump the corresponding package version in `.tap.json` on the manifest
branch. The six-hour poll dispatches missing versions; for immediate publication:

```sh
gh workflow run build.yml -R xuniv/homebrew-tap --ref main -f project=tools
```

The initial package versions are `dclone 1.76.0.1` (rclone 1.76.0 plus fork revision 1) and
`degoo 0.1.0`. No private commit hashes appear in release notes or formulae. Polling automatically
checks the manifest version; it never guesses a new package version from a private commit hash.
After intentionally withdrawing a release, disable polling/build dispatch for that project or bump
the manifest version; deleting a release alone makes it eligible for publication again.

The schedule is six-hourly and GitHub may delay it. All publications are serialized. Automatic
reruns use new builds and never overwrite an existing public release with different bytes.
