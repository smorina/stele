# Install Stele

Stele is a prototype. Its output is a software-verified candidate handoff, not
a production or fab-ready result. Stele runs locally: PDFs are not uploaded to
a service, and the browser page only talks to a private server on this
computer.

## From a GitHub release

1. Download `Stele-v….zip` from the latest GitHub release and extract it.
2. On macOS, double-click **Install Stele.command**. On Windows, double-click
   **Install Stele.cmd**. On Linux, run `./install.sh`.
3. The installer downloads the locked application dependencies, checks both
   independent GDS engines, and creates a Stele launcher.

The first install needs an internet connection and roughly 300 MB of free
space. It does not need an administrator password or a preinstalled Python.

Supported first-release platforms:

- macOS 13 or newer on Apple Silicon;
- macOS 14 or newer on Intel;
- Windows 10 or newer on x86-64;
- x86-64 Linux with glibc 2.28 or newer.

Linux ARM and Windows ARM are rejected before installation because the native
GDS dependencies do not publish compatible wheels.

## First build

1. Open **Stele**. Your normal browser opens to a local address.
2. Drop in one or more PDFs.
3. Keep the six-inch generic preset unless a mask shop has given you different
   rules.
4. Build a one-page trial first.
5. Read the verification verdict, then download the GDSII file, preview, report,
   and manifest.

Independent render-back verification is intentionally deep. Even a one-page
trial can take several minutes and several GB of memory; Stele requires that
trial before it unlocks a full-corpus build in the UI.

### Why the result lists things software did not check

That list is deliberate, not an error or unfinished report. A generic profile
can check content survival, geometry, placement, chirality, readability, and
the limits it actually defines. It cannot know a particular shop’s exposure
tone, inspection policy, required marks, or complete rule deck. Stele names
those gaps so a software pass cannot be mistaken for fabrication approval.
The applicable items must be confirmed with the mask shop.

Every run is also kept under `~/.stele/runs/` (or `%USERPROFILE%\.stele\runs`
on Windows). A run contains copied inputs, profile YAMLs, a manifest, and all
outputs. The manifest can reproduce the run from a shell:

```sh
stele build ~/.stele/runs/<run-id>/<unique-name>.manifest.yaml
```

Download names include the plate name, first source name, `trial` or `full`,
and a unique run suffix. A trial can therefore never silently overwrite the
full result, and separate uploads do not download as the same generic
`plate.gds` or `report.json`.

## Diagnostics

Choose **Diagnostics** inside the UI, or run:

```sh
stele doctor
```

The report checks Python, all native modules, the packaged blue-noise data,
run-folder write access, available disk space, and a gdstk-write to
KLayout-read round trip. A machine-readable copy is saved as
`~/.stele/doctor.json`.

## Privacy and security

The server binds only to `127.0.0.1` on a random port. Each launch uses a new
unguessable token; requests are also protected by Host and Origin checks. The
page has a restrictive content-security policy and loads no analytics, fonts,
scripts, images, or update checks from the internet.

Closing the launcher terminal stops the local server. Completed run folders
remain on disk.
