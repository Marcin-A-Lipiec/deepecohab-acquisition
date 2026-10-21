# Preparing and publishing a release

The intended GitHub repository is
**[Marcin-A-Lipiec/deepecohab-acquisition](https://github.com/Marcin-A-Lipiec/deepecohab-acquisition)**.
The first public version is **1.0.0**; the earlier 3.2.x numbers identify internal
modernization builds. No GitHub upload or public release has been performed by
the local preparation tools.

## Build and inspect locally

Use Windows x64 and the tested Python 3.12 toolchain. Stop acquisition before
changing its environment. From the maintained source directory:

```powershell
uv sync --locked --group dev --python 3.12
uv run --locked python tools/run_validation.py
uv build
uv run --locked python tools/verify_distribution.py --wheel dist/deepecohab_acquisition-1.0.0-py3-none-any.whl --sdist dist/deepecohab_acquisition-1.0.0.tar.gz
uv run --locked python tools/prepare_github_release.py --tag v1.0.0
```

The last command writes these local artifacts into `dist/`:

| Artifact | Purpose |
|---|---|
| `deepecohab_acquisition-1.0.0-py3-none-any.whl` | uv tool installation; upload as a GitHub Release asset |
| `deepecohab_acquisition-1.0.0.tar.gz` | Python source distribution; includes tests, docs and firmware |
| `deepecohab-acquisition-1.0.0-github.zip` | Clean repository source tree, including hidden GitHub configuration |
| `release-manifest.json` | Version, intended repository, license status and artifact hashes |
| `SHA256SUMS` | SHA-256 checksums for the four files above |

The source ZIP uses an explicit maintained-file inventory. It excludes
`Duplicate_test/`, `Timezone_bug/`, `DeepEcoHAB_reference_repository/`, local environments,
`.artifacts/`, raw experiment directories, `.env` files and generated binaries.
It preserves firmware and regression-fixture bytes. Linked source files and
directories are rejected. Review the ZIP listing before publishing; new sample
data must be deliberately anonymized or synthetic.

Installed-wheel/native-shortcut checks are described in [TESTING.md](../TESTING.md).
They use isolated tool directories and do not replace the existing Desktop icon.
The automated suite does not perform physical acquisition. Record acquisition-PC,
fresh-PC and downstream-analysis acceptance separately using that guide.

## Finalize the license

The owner has not selected the original-code license. No MIT or other project
license has been assumed. See [LICENSE_STATUS.md](../LICENSE_STATUS.md); bundled
reference, font and icon notices remain in force for those materials.

Before public publication, choose the license, add the full `LICENSE`, set the
project's license metadata in `pyproject.toml`, and update `LICENSE_STATUS.md`.
Rebuild and audit both packages, then require the licensing gate:

```powershell
uv run --locked python tools/prepare_github_release.py --tag v1.0.0 --require-license
```

That gate intentionally fails while the decision is pending. It checks for the
license file and project metadata; the owner must review their content. The
default command supports preparing local artifacts while that decision is open.

## Create the GitHub repository

After reviewing the artifacts and license, create an empty GitHub repository named
**deepecohab-acquisition** under **Marcin-A-Lipiec**. Use the generated clean ZIP
as the initial source tree. This avoids staging the working folder's recordings,
reference copy and local environments. For example, with Git installed:

```powershell
Expand-Archive -LiteralPath .\dist\deepecohab-acquisition-1.0.0-github.zip -DestinationPath C:\Projects
Set-Location C:\Projects\deepecohab-acquisition-1.0.0
git init -b main
git add .
git status --short
git diff --cached --stat
git commit -m "Prepare DeepEcoHab Acquisition 1.0.0"
git remote add origin https://github.com/Marcin-A-Lipiec/deepecohab-acquisition.git
git push -u origin main
```

Inspect the staged file list before the commit. Do not automatically initialize
the remote with a second README or license. The workflow in `.github/workflows/ci.yml`
runs Windows/Python 3.10, 3.11 and 3.12 software tests. Its 3.12 job builds/audits
the packages and validates the installed GUI and actual Windows shortcut. It has
read-only repository permissions and does not publish releases. The workflow
follows the [official uv GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/).

After the first hosted run passes, tag the reviewed commit:

```powershell
git tag -a v1.0.0 -m "DeepEcoHab Acquisition 1.0.0"
git push origin v1.0.0
```

Create a GitHub Release for that tag using
[RELEASE_NOTES_1.0.0.md](RELEASE_NOTES_1.0.0.md). Upload the wheel, source
distribution, clean source ZIP, manifest and checksums from the final audited
build. Prefer the passing tag workflow's `release-packages` artifact so the build
comes from the actual checked-out release commit, including Git's normal source
line-ending handling. Verify the wheel and constraints URLs in [INSTALLATION.md](INSTALLATION.md)
with a fresh Windows tool installation. Publishing the package to PyPI is optional.

## Future releases

Use a permanent Git checkout outside `dist/` for all maintained edits. Sync the
clean checkout before starting work (`git pull --ff-only origin main`); inspect
and resolve local changes rather than overwriting them. Commit reviewed source
changes and push to the existing repository. Do not reinitialize Git or use a
fresh ZIP as a new repository for each release.

After Windows Actions checks pass, tag that reviewed commit and publish its
GitHub Release with the matching audited wheel and other artifacts. Prefer
uploading artifacts while the release is a draft, then publish it once complete
and mark it as the latest stable release. The installer and update checker use
GitHub's latest published stable release, require a `vX.Y.Z` tag and expect the
matching `deepecohab_acquisition-X.Y.Z-py3-none-any.whl` asset. They do not offer
a development branch or an unfinished wheel-less release as installable.

The installer pairs the wheel with `requirements.txt` from the same immutable
tag. Do not move/reuse an already distributed version tag or replace its code;
issue a new version instead. Finish initial 1.0.0 preparation before publishing
its tag; subsequent software changes get a new version. Documentation-only
commits can keep the current software version, but do not alter old release
archives. Release notes should describe user actions, camera-wheel requirements
and any compatibility changes. Publishing a release triggers subscribers'
GitHub notifications; a source commit alone does not represent a new release.

Verify both a fresh install and an update from the previous version with the
README's PowerShell commands. Check no-release/offline errors, actual shortcut
launch, retained preferences and experiment files, and camera-wheel retention.
Remove shortcuts before `uv tool uninstall deepecohab-acquisition` and confirm
that the experiment files remain. Keep this evidence separate from physical
hardware acceptance. The CI workflow prepares artifacts but does not publish.

Update `deepecohab_recorder/__init__.py` once, add the changelog entry, refresh
versioned installation URLs/release notes, and repeat the checks. The workflow
reads the centralized version and checks pushed tags against it. Keep scientific
schema versions independent; only change them when the public data contract
changes. Preserve the dependency lock unless a reviewed dependency update is
intended. Rebuild after every source or documentation change so the archive
audit still matches the release commit.
