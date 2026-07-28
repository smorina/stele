# Release checklist

The release artifact is a source bundle with double-click installers. It uses
the committed `uv.lock`, so users get the same managed Python 3.12 and native
dependency versions tested here.

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
- the public snapshot contains the release workflow and all four installers.

After confirmation it publishes the curated snapshot to the public repository,
creates an annotated tag there, waits for GitHub Actions, and verifies that the
release contains both `Stele-vX.Y.Z.zip` and the matching wheel. `--yes` skips
the confirmation for an already supervised automation environment.

## Version preparation

Before running the script, update the version in `pyproject.toml` and
`src/stele/__init__.py`, run `uv lock`, and commit those changes through the
normal private-repository PR workflow. Never reuse a version that has been
shared outside the project, even if it was not attached to a GitHub release.

## Final acceptance

Download the ZIP from the completed public GitHub release and test it on at
least one clean macOS or Windows user account. The acceptance path is:
double-click installer → diagnostics pass → choose PDF → one-page trial →
understandable verdict → download GDS and report.

The release remains AGPL-3.0-or-later source distribution. The installer does
not create an opaque combined executable; it keeps the full corresponding
source beside the managed environment in `~/.stele/app`.
