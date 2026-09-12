# stele

A compiler from PDF to chrome-on-glass photomask layouts

<!-- HERO MEDIA (add before publishing):
  1. Copy the full-plate overview and a microscope-view crop into docs/media/:
       cp out/patent_demo.gds.preview.png docs/media/plate-preview.png
       cp out/patent_demo.gds.PAGE_0000.sim.png docs/media/microscope-view.png
  2. Uncomment:
  ![Full 6-inch plate, 28 pages placed](docs/media/plate-preview.png)
  ![Simulated view through the reader microscope](docs/media/microscope-view.png)
  3. When the 30-second demo video exists (storyboard kept outside the
     repository, alongside ../document.pdf), embed it here instead and move
     the stills below the fold.
-->

## about 

PDF → photomask (GDSII) archive compiler, realizing the pseudopage-plate
archive of US Patent 11,993,421 B2: full pages optically reduced ~109:1 onto
chrome-on-glass plates (~3,700 pages per 6″ plate), readable with a simple
microscope. The patent specifies the plates but not a toolchain for producing
them; `stele` is that compiler — PDF in, verified GDSII mask layout out.

If the premise sounds eccentric, consider the company it keeps: GitHub froze
every public repository on archival film in Svalbard, the Long Now Foundation
microetches languages onto nickel disks, Microsoft writes data into quartz,
and Memory of Mankind fires it into ceramic in a salt mine. Chrome on glass
sits in the same family, with one practical advantage: photomask production
is an ordinary industrial service, so a plate can be written today without
inventing any hardware.

**STATUS: PROTOTYPE.** Output is a software-verified candidate handoff, not a
production or fab-ready result. See
[issue #2](https://github.com/smorina/stele/issues/2) for the full
development plan and status.

**LICENSE:** [AGPL-3.0-or-later](LICENSE). `stele` is an independent
implementation; US 11,993,421 B2 belongs to its inventors, and nothing in
this repository grants rights to practice the patent.

**AI USE:** Built leveraging Claude (Fable 5) via Claude Code, and GPT 5.6 Sol.

## The interesting bits

- The EDA toolchain from the outside: GDSII hierarchy and SREF discipline,
  database-unit snapping, DRC, and the gdstk / KLayout Python APIs.
- Verification methodology: independent engines checking each other,
  planted-defect calibration instead of hand-picked thresholds, and report
  semantics that can't overstate what was checked.
- Computational geometry at scale: ~10⁶ polygons per page, hierarchical
  render-back instead of flattening, blue-noise halftoning under DRC
  constraints.
- Basic optics: Airy PSFs, contrast criteria, and readability as a measured
  quantity rather than a judgment call.

## Proving nothing was lost

A compiler bug here wouldn't crash — it would drop a glyph. So no engine
checks its own output: **gdstk** writes the GDS, **KLayout** reads it back,
and **pypdfium2** renders the reference page (the build itself renders with
PyMuPDF, a different engine again). Each page is then compared by XOR with
tone hysteresis, connected-component structural defects, measured stroke
widths against the mask writer's minimum feature, DRC-lite checks, a
placement audit, and a chirality glyph that fails mirrored plates. The
thresholds are calibrated by planted-defect tests — a dropped glyph, a
phantom blob, shifted text must each be caught while a clean build passes.
Results are three-state (`pass` / `pass_with_warnings` / `fail`), plus an
explicit `unverified` for anything built with checks skipped.

## Quickstart

### For people who do not use a terminal

On macOS, download and extract the app ZIP for your processor from the latest
GitHub release:

- `macos-arm64` for Apple Silicon (M1 or newer);
- `macos-x86_64` for Intel.

Double-click **Stele.app**. It is portable and includes Python, both GDS
engines, and every other prerequisite; it does not install anything. Because
this hobby project does not use a paid Apple Developer ID, the first launch
needs one Gatekeeper approval under **System Settings → Privacy & Security →
Open Anyway**. Subsequent launches of that copy are normal double-clicks.
If Stele is already running, double-clicking it again reopens the same private
browser session instead of starting another server. Use **Quit Stele** in the
page when finished; Stele will not quit in the middle of a build.

On Windows x86-64, download the `windows-x86_64` ZIP, extract the complete
**Stele** folder, and double-click **Stele.exe**. It is likewise portable and
self-contained. Windows may show **Windows protected your PC** because this
hobby build is unsigned; for a ZIP downloaded from the official release,
choose **More info**, confirm the app name, then **Run anyway**. Keep the
`_internal` folder beside the executable.

Linux users download the source ZIP and run `./install.sh`. The source ZIP
also retains **Install Stele.cmd** as an optional managed-runtime fallback on
Windows.

The launcher opens a private local browser UI: drop in PDFs, see plate capacity
before doing expensive work, build a one-page trial, then build and independently
verify the complete plate. Inputs, profiles, manifest, GDS, preview, and report
stay together under `~/.stele/runs/`. See [the installation guide](docs/install.md).

### Command line

```sh
uv sync
uv run stele ui                                  # local browser interface
uv run stele doctor                              # installation diagnostics
uv run stele validate jobs/patent_demo.yaml   # cross-profile checks
uv run stele calc jobs/patent_demo.yaml       # capacity / resolution math
uv run stele build jobs/patent_demo.yaml      # -> out/patent_demo.gds + report + preview
uv run stele verify jobs/patent_demo.yaml     # re-verify an existing GDS
uv run stele simulate jobs/patent_demo.yaml   # through-the-microscope view of a page
```

The UI exposes the decisions with visible consequences, grouped behind
collapsible sections: plate size (6", 5", 150 mm, 125 mm, 4", custom) and
unusable border; clear- or dark-field tone and mirroring; page size on glass,
gutters (none by default) and the magnification ladder; title text, height,
band and alignment; the guide band's description text, size and alignment;
and the microscope's magnification, NA, wavelength and contrast criterion.
A live plate sketch and optics estimates update as you type, every finished
run keeps its settings for reuse, and a microscope panel re-renders any built
page through other optics without rebuilding. The CLI and
[reference](docs/reference.md) remain the path for custom vendor profiles and
the remaining expert fields. [docs/usability.md](docs/usability.md) records
the analysis, decisions, shipped scope, and follow-on plan.

Beyond text: `image_mode: dither` renders continuous-tone images as
DRC-clean blue-noise halftones; `color_mode: rgb_triad` emits the patent's
projector triads; `tier_scales: [4.0, 1.0]` builds the naked-eye ->
microscope magnification ladder; page sets overflow onto numbered plates
automatically. See docs/reference.md for the full manifest/profile surface.

## Inspect and photograph the flagship demo

The flagship job compiles the patent's own PDF onto one 6″ plate. Its source
file is intentionally kept outside this repository: place it at
`../document.pdf` relative to the repository root before building.

### 1. Build the plate

```sh
uv sync
uv run stele build jobs/patent_demo.yaml
```

The build writes:

- `out/patent_demo.gds` — hierarchical GDSII mask layout;
- `out/patent_demo.gds.report.json` — provenance and verification report;
- `out/patent_demo.gds.preview.png` — full-plate overview.

On macOS, open the overview with:

```sh
open out/patent_demo.gds.preview.png
```

### 2. Inspect the GDS in KLayout

[KLayout](https://www.klayout.de/) is a mask-layout viewer/editor that reads
GDSII hierarchy, cells, layers, and micron-scale geometry. `uv sync` installs
its Python API for stele's verification code, but the graphical desktop
application is a separate installation.

On macOS with Homebrew:

```sh
brew install --cask klayout
open -a KLayout out/patent_demo.gds
```

For other platforms, use the
[official KLayout downloads](https://www.klayout.de/build.html). Once the GDS
is open:

1. Press `*` to display the full cell hierarchy.
2. Press `F2` to fit the plate in the window.
3. Use the mouse wheel, or right-drag a rectangle, to zoom from plate to
   pseudopage to individual glyph polygons.
4. To jump directly to a page, select `PAGE_0000` in the left hierarchy
   panel, right-click, and choose **Show as top**.
5. Keep GDS layer `1/0` visible in the layer panel on the right.
6. Choose **File → Screenshot** to save the current canvas as a PNG.

The relevant KLayout manual pages cover
[hierarchy depth](https://www.klayout.de/doc/manual/hier.html),
[cell selection](https://www.klayout.de/doc/manual/cell.html),
[zooming](https://www.klayout.de/doc/manual/zoom.html), and
[screenshots](https://www.klayout.de/doc/manual/screenshots.html).

### 3. Simulate the bundled microscope

`stele simulate` does not require the KLayout desktop application. It reads
the built GDS with KLayout's Python API, applies the reader profile's Airy PSF,
downsamples to eye-limited resolution, measures stroke contrast, and writes a
PNG:

```sh
uv run stele simulate jobs/patent_demo.yaml \
  --cell PAGE_0000 \
  --out out/page-1-microscope.png

# macOS
open out/page-1-microscope.png
```

`--cell` selects a materialized page (`PAGE_0000`, `PAGE_0001`, …).
`--region x0,y0,width,height` optionally selects a region in pseudopage
micrometres; without it, the command renders a central 400 × 300 µm region.
Run `uv run stele simulate --help` for the complete option list. The command
exits nonzero if no measured stroke bin meets the reader profile's contrast
criterion.

## Tests

```sh
uv run pytest -m "not slow"   # fast tier (~30 s)
uv run pytest                 # + whole-plate golden builds and explicit RSS budgets

# macOS only: build and smoke-test a portable app for this Mac's architecture
packaging/build-macos-app.sh
```

## Layout

- `src/stele/` — frontends (raster), IR (document-space µm), layout engine,
  GDSII backend, verify stage. See
  [issue #2](https://github.com/smorina/stele/issues/2) for the architecture.
- `profiles/` — fab / reader / content / layout YAMLs with provenance
  comments (patent example vs engineering assumption).
- `jobs/` — job manifests; `testdata/gen/` — synthetic corpus generators.
