# Release checklist

Each release publishes self-contained unsigned applications for macOS Apple
Silicon, macOS Intel, and Windows x86-64, plus the source bundle used by the
Linux and optional Windows installers and the Python wheel. The portable apps
contain the Python 3.12 runtime and the exact native dependency versions from
the committed `uv.lock`; end users do not install uv, Python, or packages.

## After merging private work

Start from a clean private `main` that is synchronized with `origin/main`, then
choose one publishing path:

```text
Private PR merged
├─ Source update only → tools/publish-to-public.sh
└─ New downloadable release → bump version → tools/release.sh
```

For a source-only update, preview and publish the curated public snapshot:

```sh
tools/publish-to-public.sh --dry-run
tools/publish-to-public.sh --push -m "Publish: brief description"
```

This updates public `main` without creating a tag, installer ZIP, wheel, or
GitHub release.

When users should receive new downloadable artifacts, use the release path
below. Do not run `publish-to-public.sh` first: `release.sh` includes the public
source publication, then creates and verifies the tagged release.

## Normal maintainer workflow

From a clean, synchronized `main` in the private development repository, run:

```sh
tools/release.sh v0.2.0
```

That command performs every local check, previews the public snapshot, and asks
for one confirmation before it changes either remote. To stop after the preview,
use the optional dry-run mode:

```sh
tools/release.sh --dry-run v0.2.0
```

The script refuses to proceed unless:

- both version declarations match the requested `vX.Y.Z`;
- private `main` and the public clone are clean and synchronized;
- the requested public tag does not already exist;
- `uv.lock` is current;
- lint and the fast test tier pass;
- a fresh wheel contains the UI and blue-noise data; and
- the public snapshot contains the release workflow, macOS and Windows
  packaging recipes, shared packaged-app smoke test, and fallback installers.

After confirmation it publishes the curated snapshot to the public repository,
creates an annotated tag there, waits for GitHub Actions, and verifies that the
release contains `macos-arm64` and `macos-x86_64` app ZIPs, the source ZIP,
the `windows-x86_64` portable ZIP, and the matching wheel. `--yes` skips the
confirmation for an already supervised automation environment.

The public tag starts four packaging paths:

- Linux verifies source, tests, and builds the wheel/source ZIP;
- a standard Apple Silicon runner builds and smoke-tests the arm64 app;
- a standard Intel runner builds and smoke-tests the macOS x86_64 app; and
- a standard Windows runner builds and smoke-tests the Windows x86-64 app.

The release is created only after all four succeed. PyInstaller's one-folder
form is used on both operating systems. Finder presents the macOS bundle as one
`Stele.app`; the Windows ZIP exposes `Stele.exe` beside an `_internal`
dependency folder. `ditto` preserves macOS symlinks and metadata, while
PowerShell creates the Windows ZIP and SHA-256 file.

## Version preparation

Before running the script, update the version in `pyproject.toml` and
`src/stele/__init__.py`, run `uv lock`, and commit those changes through the
normal private-repository PR workflow. Never reuse a version that has been
shared outside the project, even if it was not attached to a GitHub release.

## Final acceptance

Download the applicable ZIPs from the completed public GitHub release and test
them on clean macOS user accounts without Python, uv, or developer tools. At a
minimum, test Apple Silicon for each release; test Intel whenever an Intel Mac
is available. The acceptance path is: extract → double-click Stele.app →
one-time Gatekeeper approval → browser opens → diagnostics pass → choose PDF
→ one-page trial → understandable verdict → download GDS and report.

The Windows runner automatically exercises packaged imports, the GDS
round-trip diagnostic, local HTTP, two launches against one server, and clean
Quit. When a clean Windows x86-64 machine is available, also test: extract the
complete folder → double-click Stele.exe → documented SmartScreen path →
browser opens → diagnostics pass → one-page trial → understandable verdict →
download GDS and report. A physical Windows machine is desirable for this
human-facing acceptance path, but is not required to prove the packaged
runtime and lifecycle on every release.

The Mac app is intentionally unsigned because this hobby project does not pay
for an Apple Developer ID. Do not add quarantine-removal commands or suggest
disabling Gatekeeper. The release notes and installation guide must continue
to explain Apple's per-app **Open Anyway** path. Also confirm that launching
the app a second time reopens the first session, that only one server remains,
and that **Quit Stele** stops it after refusing to interrupt an active build.

The Windows app is also intentionally unsigned. Do not claim that SmartScreen
will be absent. Release notes and the installation guide must explain **More
info → Run anyway**, state that managed computers may block unsigned apps, and
tell users to proceed only with the ZIP from the official release.

The release remains AGPL-3.0-or-later. The source ZIP and public tag published
beside the application bundles are the corresponding source for those bundles.
