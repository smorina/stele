# Install Stele

Stele is a prototype. Its output is a software-verified candidate handoff, not
a production or fab-ready result. Stele runs locally: PDFs are not uploaded to
a service, and the browser page only talks to a private server on this
computer.

## macOS: portable application

1. Download the app ZIP matching the Mac from the latest GitHub release:
   - `macos-arm64` for a Mac whose **About This Mac** window lists an Apple
     chip such as M1, M2, M3, or newer;
   - `macos-x86_64` for a Mac whose **About This Mac** window lists an Intel
     processor.
2. Extract the ZIP and double-click **Stele.app**.
3. The first time, macOS will block this unsigned hobby-project build. After
   attempting to open it, open **System Settings → Privacy & Security**, scroll
   to Security, choose **Open Anyway**, and confirm **Open**.
4. The normal browser opens to Stele's private local page. Future launches of
   that downloaded copy are ordinary double-clicks.

Closing the browser tab does not stop Stele. Double-clicking **Stele.app** while
it is already running reopens the same private browser session; it does not
start a second server. Choose **Quit Stele** in the page when finished. The app
refuses to quit while a build is queued or running, so cancel it or wait for it
to finish first.

Stele.app is portable. It includes Python, gdstk, KLayout, OpenCV, both PDF
engines, and every other runtime prerequisite. It does not require an
administrator password, developer tools, uv, a preinstalled Python, or an
internet connection after download. It can run from Downloads or be moved to
Applications.

Gatekeeper approval cannot be removed from a downloaded unsigned executable.
Stele does not ask users to run a command that disables Gatekeeper or removes
the quarantine attribute.

Supported macOS platforms:

- macOS 13 or newer on Apple Silicon;
- macOS 14 or newer on Intel;

Each release app is built and smoke-tested on the same processor architecture
as its bundled native dependencies. The smoke test imports every required
engine, loads packaged data, and performs a gdstk-write to KLayout-read
round trip before the ZIP is published.

## Windows: portable application

1. On an x86-64 computer running Windows 10 or newer, download the release ZIP
   whose name ends in `windows-x86_64.zip`.
2. Extract the complete **Stele** folder. Do not move **Stele.exe** away from
   its `_internal` folder.
3. Double-click **Stele.exe**. The normal browser opens to Stele's private
   local page.
4. Because this hobby-project build is unsigned, Windows may show **Windows
   protected your PC** on first launch. If the ZIP came from Stele's official
   GitHub release, choose **More info**, confirm that the app name is Stele,
   then choose **Run anyway**. Some managed computers prohibit unsigned apps.

The Windows app is portable and self-contained. It includes Python, both GDS
engines, both PDF engines, OpenCV, and every other runtime prerequisite. It
installs nothing, needs no administrator password, and works without an
internet connection after download.

Closing the browser does not stop Stele. Double-clicking **Stele.exe** again
reopens the same private session instead of starting a second server. Choose
**Quit Stele** in the page when finished; active builds must finish or be
cancelled first.

Windows on Arm is unsupported because gdstk, KLayout, and OpenCV do not publish
compatible Arm wheels.

## Linux and optional managed-runtime installers

On x86-64 Linux with glibc 2.28 or newer, download and extract the release's
source ZIP, then run `./install.sh`.

The source ZIP also retains **Install Stele.cmd** as a fallback for Windows
x86-64 users who prefer a managed uv environment. The managed-runtime path
needs an internet connection on first installation and roughly 300 MB of free
space, but no administrator password or preinstalled Python.

Linux Arm is rejected before installation because gdstk does not publish a
compatible wheel.

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

The local server runs as a background Stele process after the launcher exits.
Its per-user lock and private session file are stored under `~/.stele/` on
macOS/Linux or `%USERPROFILE%\.stele\` on Windows. The session file contains
the loopback URL needed to reopen the page and is protected by the user's file
permissions. Choosing **Quit Stele** stops the server and removes that session.
Completed run folders remain on disk.
