# stele for non-programmers: install, UI, and plain language

**Status (2026-07-27): PROTOTYPE.** The first operability release is
implemented, but neither the application nor its output is production or
fab-ready. The original proposal below remains the design record; §8 now
distinguishes shipped work from later phases.

**Scope.** The development plan
([issue #2](https://github.com/smorina/stele/issues/2)) listed "GUI" under *Out
of scope*. That plan is ours to change, and it is hereby changed: **a UI is in
scope, as M8 — operability.** The original exclusion was right for M0–M7 —
the product being proved was a verified compiler, and a GUI would have been
surface area competing with verification depth. That work is done, and its
verification story is the project's strongest asset. What it lacks is anyone
outside this repository who can run it. M8 fixes that and nothing else: it does
not reopen the vector frontend or any other cut item, and it adds no new
compute path (§3.5).

### Implementation verdict

The initial plan got the important product boundaries right: local-only
processing, a browser as a thin manifest-and-runner layer, real report status
as the only verdict source, permanent candidate-handoff wording, reproducible
run folders, `uv` rather than a frozen native binary, and defensive localhost
security. Those choices shipped.

It over-scoped the first release in two places. A generated editor for every
profile field serves technical users better through YAML and the existing CLI,
while burdening the path for an archivist. The mask-shop ZIP is valuable only
after the basic result language has been used with real shops. Both are
deferred. The first release instead concentrates on the complete first-use
loop: double-click install, diagnostics, drag/drop, page selection, live
capacity, trial build, full verified build, progress/cancel, plain-language
verdict, preview/downloads, and a reproducible run folder.

---

## 1. Who this is for

Three concrete people, in priority order:

1. **The archivist / librarian.** Has a PDF corpus and a preservation mandate.
   Comfortable with Word, Excel, a scanner, and a DAM system. Has never
   opened a terminal on purpose. Wants: *"these 400 documents, on plates, with
   proof nothing was dropped."*
2. **The demonstrator.** Patent holder, investor, journalist, conference
   attendee. Needs to go from zero to a plate preview and a
   through-the-microscope image in ten minutes, on a borrowed laptop, on
   conference wifi. Judges the project by whether that works.
3. **The mask-shop contact.** Receives a GDS and a report. Not a stele user at
   all, but the *report* is a user interface, and today it is a 3 MB JSON file.

Deliberately **not** in scope: someone who wants to author new fab profiles
from a vendor rule deck, tune blue-noise spectra, or add a backend. That is
what the CLI, `docs/reference.md`, and the source are for. The UI serves
people who want to *use* the compiler, not extend it.

---

## 2. What actually blocks them today

An honest audit, ordered by how early it stops someone.

| # | Blocker | Detail |
|---|---|---|
| 1 | **No install path** | `uv sync` presupposes `uv`, a shell, a cloned repo, and knowing what a lockfile is. There is no download, no double-clickable anything. |
| 2 | **Ten native dependencies** | gdstk, klayout, opencv, pymupdf, pypdfium2, shapely, numpy, pillow, pydantic, pyyaml — all wheels, but a source build of any one of them is an unrecoverable error for this audience. |
| 3 | **Python 3.12 exactly** | `requires-python = ">=3.12,<3.13"`. A user with 3.13 preinstalled gets a resolution failure with no actionable message. |
| 4 | **Five YAML files, all path-coupled** | A job needs `inputs[].path`, four `profiles.*` paths, and `output.gds`, all resolved relative to the manifest file. The flagship demo's input is `../../document.pdf` — two levels up from `jobs/`, and *intentionally absent from the repo*. |
| 5 | **Jargon as the primary interface** | To change anything, you must have an opinion about `numerical_aperture`, `contrast_criterion` (Michelson), `min_space_um`, `dbu_nm`, `datatype`, `max_vertices_per_polygon`, `tier_scales`, `dither_pitch_um`. There are 57 settable fields and no defaults-explained layer. |
| 6 | **Five commands, order mattering** | `validate` → `calc` → `build` → `verify` → `simulate`. Nothing says that `calc` is the cheap one you should run first, or that `build` already runs `verify` unless told not to. |
| 7 | **The report is JSON** | The authoritative verdict is `verify.gates.status` nested in a file that can exceed a megabyte. The CLI prints a good summary; nobody sees it after the scrollback is gone. |
| 8 | **Results live in `out/`** | Relative to the manifest. On a double-clicked app there is no "current directory" to reason about. |
| 9 | **Silent resource cliffs** | 900 DPI letter page ≈ 75.7 MP. Before the memory pass, a verified build reached **11.2 GB peak RSS**. The current dense-page probe is 2.60 GB and a streamed 120-page build is 0.61 GB, but full-plate verified performance remains explicitly **unproven** (M6). A non-programmer still needs a calibrated warning before starting a large job. |
| 10 | **Exit codes as the contract** | 0 / 1 / 2, plus the crucial subtlety that `unverified` also exits 0. Invisible in a GUI unless deliberately surfaced. |
| 11 | **KLayout is a second install** | The README's inspection workflow needs a separate desktop app, Homebrew, and six keyboard shortcuts. |

Blockers 1–4 stop everyone. 5–8 make the tool unusable *after* it runs. 9–10
are where a naive UI would do real damage.

---

## 3. Guardrails: what the UI must not do

This project's distinguishing feature is that it refuses to overstate what it
checked — three-state status, an explicit `unverified`, `UNENFORCED_FAB_FIELDS`
disclosed in every report, provenance comments separating patent example from
engineering assumption. A GUI is exactly the layer where that discipline
usually dies, replaced by a green checkmark. Non-negotiables:

1. **The UI never invents a verdict.** It renders `verify.gates.status` and
   nothing else. `pass_with_warnings` is never drawn as a pass. `unverified`
   is never drawn as anything but unverified.
2. **"Candidate handoff", permanently visible.** Every result screen and every
   exported artifact carries the same sentence the README carries: this is a
   software-verified candidate handoff, not fab-ready output, until a vendor
   rule deck is encoded and a coupon validated. It is not a dismissible
   dialog.
3. **Provenance survives the UI.** Every UI run writes a real job manifest and
   real profile YAMLs to disk before building, and the run is reproducible by
   `stele build <that manifest>`. The UI is a *manifest editor and process
   runner*, not a second configuration path. Corollary: no inline-profile
   support in the manifest schema — `build.py` hashes profile *files* into
   `profile_hashes`, and that hash chain must not get a hole punched in it for
   GUI convenience.
4. **Assumption provenance is shown, not stripped.** The shipped YAMLs mark
   every value `# source: patent example` or `# assumption: engineering
   default`. The UI must surface that distinction per field — an
   engineering-default NA is not a fact about the patent, and a user changing
   it deserves to know which kind of number they are touching.
5. **No new compute path.** The UI calls `build_job`, `verify_plate`,
   `plan_plate`, `validate_profiles`. It reimplements nothing. If a number can
   be computed two ways, the UI is not one of them.
6. **Nothing leaves the machine.** No telemetry, no CDN, no fonts, no update
   check. A preservation tool for sensitive archives that phones home is a
   non-starter, and offline operation is a hard requirement anyway (§4.5).

---

## 4. Install and launch

### 4.1 Strategy: `uv` as the installer, not as a prerequisite

`uv` already solves the hard parts — it installs a managed CPython 3.12
itself, needs no admin rights, no system Python, and no compiler, and
`uv sync --frozen` reproduces the exact locked wheel set. The gap is only that
the user must know to install it and must type commands afterwards.

So: **ship a bootstrap script that installs `uv`, syncs, and creates a
launcher.** One command, or one double-click.

```
install.sh / install.ps1
  ├─ detect OS + arch, refuse unsupported combos with a real explanation
  ├─ install uv to ~/.local/bin (or %LOCALAPPDATA%) if absent — no admin, no PATH surgery
  ├─ fetch/copy stele into ~/.stele/app
  ├─ uv sync --frozen            (pulls managed CPython 3.12 + 10 wheels)
  ├─ stele doctor                (see §4.4 — fails loudly here, not later)
  └─ create launcher:
       macOS   ~/Applications/Stele.command  + optional .app wrapper
       Linux   ~/.local/share/applications/stele.desktop
       Windows Desktop\Stele.lnk  →  pythonw -m stele.ui
```

The launcher runs `stele ui`, which starts the local server and opens the
browser (§5). The user never sees a terminal.

`~/.stele/` layout:

```
~/.stele/
  app/            the synced checkout + .venv (replaceable wholesale on update)
  runs/           one folder per build: manifest + profiles + gds + report + previews
  profiles/       user-saved presets (copied from app/profiles on first run)
  wheels/         optional offline wheelhouse (§4.5)
  doctor.json     last self-check result
```

Keeping runs under `~/.stele/runs/<timestamp>-<name>/` retires blocker 8: every
artifact for a run is in one folder the UI can open in Finder/Explorer, and
that folder *is* a valid CLI working directory.

### 4.2 Platform support, from the lockfile

Read off `uv.lock` (pinned: gdstk 1.0.1, klayout 0.30.9,
opencv-python-headless 5.0.0.93, pypdfium2 5.12.1). "Wheels" below means every
one of the ten dependencies resolves to a binary wheel — no compiler needed.

| Platform | Wheels | Notes |
|---|---|---|
| macOS arm64 (Apple Silicon) | ✅ all | gdstk via `macosx_10_13_universal2`; opencv/pypdfium2 require **macOS 13+** |
| macOS x86_64 (Intel) | ✅ all | opencv wheel is `macosx_14_0_x86_64` → **macOS 14+** on Intel |
| Linux x86_64 (glibc) | ✅ all | gdstk/klayout are manylinux_2_28 → **glibc ≥ 2.28** (Ubuntu 20.04+, Debian 10+, RHEL 8+) |
| Linux x86_64 (musl/Alpine) | ✅ all | musllinux_1_2 wheels present for all ten |
| Windows x86_64 | ✅ all | Windows 10+ |
| **Linux aarch64** | ❌ | **gdstk has no aarch64 wheel** — source build (C++ toolchain, zlib, qhull). Out of scope for the installer; document, don't attempt. |
| **Windows arm64** | ❌ | gdstk, klayout, opencv all missing `win_arm64`. Unsupported. |

The installer must detect the two ❌ rows *up front* and say exactly why,
naming the package. A 20-minute failed compile is a worse outcome than a
one-line refusal.

### 4.3 Packaging alternatives, and why not

| Option | Verdict |
|---|---|
| **uv bootstrap script** (proposed) | One command; installs its own Python; reproducible via the existing lockfile; trivially updatable; no signing infrastructure. Cost: a script per OS family, and the first run downloads ~200 MB. |
| PyInstaller / Nuitka one-file binary | Tempting, rejected for now. klayout + opencv + pymupdf native extensions make hook maintenance a standing tax; needs a CI build matrix per OS; needs Apple notarization and a Windows code-signing certificate or users see malware warnings — and unsigned binaries are *worse* for a trust-critical tool. Revisit only if a signed-release pipeline exists. |
| conda / pixi | Solves native deps well, but adds a second package manager to explain and duplicates the lockfile as a source of truth. |
| Docker | Fine for the mask-shop/CI path, hostile for persona 1 and 2: Docker Desktop install, licensing, file-sharing permissions, and no native file dialogs. Document as an advanced option. |
| Hosted web service | Rejected outright. Users' archival corpora are exactly the documents they will not upload, and multi-GB RSS per build makes it an operational trap. |

**Licensing consequence of distribution.** stele is AGPL-3.0-or-later and
PyMuPDF is AGPL (risk 9 in the plan: "*if distributed*"). The bootstrap
approach keeps distribution as source + PyPI wheels, which is the cleanest
posture. Any future single-file binary is a *distribution of a combined work*
and needs the corresponding-source obligation designed in, not discovered
afterwards.

### 4.4 `stele doctor`

A new subcommand, run automatically by the installer and reachable from the UI.
It is the difference between "it didn't work" and a fixable report.

Checks: Python version and interpreter path; each of the ten imports with its
resolved version and file path (catching the classic broken-opencv case);
`klayout.db` actually loadable (it is the verification engine — if it is
broken, nothing can be verified); a 1-page synthetic build end-to-end using
`testdata/gen/synthetic.py`; total and available RAM; free disk on the volume
holding `~/.stele/runs`; the blue-noise asset hash from
`assets/bluenoise/vnc64.npy`; write permission on `~/.stele`; whether the
KLayout desktop app is installed (informational only — never required).

Output: human-readable to the terminal, `~/.stele/doctor.json` for support,
and a rendered panel in the UI. One "Copy diagnostics" button that produces
text safe to paste into an issue — versions and paths, no document names.

### 4.5 Offline and air-gapped install

A 5,000-year-archive tool used by institutions will be run on machines without
internet. `uv` supports this properly, so the installer should too:

```sh
# on a connected machine
uv export --frozen --no-dev -o requirements.txt
uv pip download -r requirements.txt -d wheels/ --python-platform <target> --python-version 3.12
uv python install 3.12 && cp -R "$(uv python dir)" python/     # managed interpreter
# → copy stele/ + wheels/ + python/ to media

# on the target
./install.sh --offline --wheels ./wheels --python ./python     # uv sync --offline --find-links
```

Acceptance: a full install and a one-page build on a machine with networking
physically disabled.

### 4.6 Updates and uninstall

Update: `stele update` re-fetches the app into `~/.stele/app.new`, syncs, runs
`doctor`, and swaps only on success — the previous tree stays as `app.old` for
rollback. Never touches `runs/` or `profiles/`.

Uninstall: `stele uninstall` removes `~/.stele/app` and the launcher, and
*asks* about `runs/` and `profiles/`. A tool for people preserving documents
must never delete their outputs without being told to.

---

## 5. The UI

### 5.1 Architecture

Stdlib only — no FastAPI, no uvicorn, no npm. `http.server.ThreadingHTTPServer`
plus one self-contained HTML file. Rationale: the dependency set is the thing
the installer has to survive, and adding a web framework to make ~10 endpoints
work is a bad trade. It also means the UI cannot rot independently of the
Python that ships with it.

```
src/stele/ui/
  __init__.py
  server.py        ThreadingHTTPServer, routing, auth token, JSON helpers
  runner.py        build/verify/simulate in a worker thread; progress queue; cancel flag
  schema.py        pydantic models  ->  form schema  (generated, never hand-copied)
  labels.py        plain-language label/help/unit/provenance per field  (the human layer)
  verdict.py       verify.gates  ->  plain-language findings + suggested actions
  estimate.py      page count + settings  ->  time/RAM/disk estimate from a calibration table
  runs.py          ~/.stele/runs bookkeeping, manifest+profile emission
  static/app.html  single page: HTML + CSS + JS, inlined, no external requests
  static/*.svg     inlined icons
```

`stele ui [--port N] [--no-browser]` becomes a sixth CLI subcommand. The
existing five are untouched.

**Security.** A localhost HTTP server that runs builds and reads local files is
a real attack surface, and "it's only localhost" is not a defence — any process
on the machine, and any web page the user visits (DNS rebinding), can reach it.
Therefore:

- bind `127.0.0.1` on an ephemeral port; never `0.0.0.0`;
- generate a 128-bit token per process, pass it in the opened URL, require it
  on every request (header or query param);
- validate `Origin`/`Host` against the bound address — the anti-rebinding
  check;
- `POST` for anything with an effect, with the token in a header, so a
  cross-origin form cannot trigger a build;
- no arbitrary path reads: file choices come from a native picker (§5.3) and
  are resolved server-side into an allowlist; `/api/artifact` serves only files
  under `~/.stele/runs`, path-normalized, symlinks rejected;
- `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline';
  script-src 'unsafe-inline'; img-src 'self' data:` — enforces the
  no-external-requests promise;
- exit after an idle timeout with no client connected.

**Endpoints.**

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | the single page |
| GET | `/api/schema` | generated form schema: fields, types, ranges, enums, defaults, labels, help, units, provenance |
| GET | `/api/presets` | shipped + user profile bundles |
| POST | `/api/inspect` | PDF page count, page sizes, rotation, whether it has images/color — `collect_pages` only, no rendering |
| POST | `/api/calc` | `validate_profiles` + `plan_plate`: slots, plates needed, reduction, warnings. Sub-second; drives the live panel |
| POST | `/api/estimate` | predicted wall time, peak RSS, output size (§5.6) |
| POST | `/api/run` | write manifest + profiles into a run folder, start worker, return run id |
| GET | `/api/run/<id>/events` | Server-Sent Events: progress, log lines, completion (SSE is 30 lines on `BaseHTTPRequestHandler`; no websocket library) |
| POST | `/api/run/<id>/cancel` | cooperative cancel |
| GET | `/api/run/<id>/report` | the report JSON, plus the plain-language verdict |
| GET | `/api/artifact?run=<id>&name=…` | preview PNG, heatmap, GDS download |
| POST | `/api/simulate` | microscope view for a chosen page/region |
| GET | `/api/doctor` | last/fresh self-check |

### 5.2 Progressive disclosure — and "all possible customizations"

Three levels, one settings model:

- **Simple** — five controls: the PDF(s), which pages, a plate preset, a plate
  name, and Build. Everything else is preset-supplied.
- **Standard** — the dozen decisions with visible consequences: plate size,
  writer minimum feature, page-fit mode, images as halftone vs threshold,
  colour mode, magnification tiers, nav band, mirroring, render DPI.
- **Expert** — **every** field of `FabProfile`, `ReaderProfile`,
  `ContentPolicy`, `LayoutSpec`, `InputSpec`, `OutputSpec`. Grouped by profile,
  each with label, unit, help text, range, default, and provenance marker;
  each with a "reset to preset" affordance; raw YAML shown side-by-side and
  editable, validated on every keystroke by the actual pydantic models. This is
  also the only place the verification toggle lives (§5.9).

The Expert panel is *generated* from `model_json_schema()`, so a new profile
field appears in the UI automatically. The human text lives in `labels.py`
keyed by `<model>.<field>`, and **a unit test asserts that every field of every
profile model has a label entry** — the mechanism that stops the UI from
drifting out of sync with the code. A new field without plain-language text
fails CI rather than shipping as a bare identifier.

`channel_transforms` (a nested dict of R/G/B → dx/dy/scale/rotation) is the one
field that does not fit a flat form; it gets a small purpose-built editor with
the constraints from the model validator (`scale ∈ (0, 2]`, finite, R/G/B only)
enforced client-side *and* server-side.

### 5.3 File selection

Browsers cannot hand a server a real path. Options, in order of preference:

1. **Native picker via the OS**, invoked server-side on click — `osascript` on
   macOS, `zenity`/`kdialog` on Linux, a PowerShell `OpenFileDialog` on
   Windows. No new dependency; returns a real path; feels native.
2. **Drag-and-drop upload** into `~/.stele/runs/<id>/input/` as fallback. Costs
   a copy of the PDF, which for a 500 MB scan is real but acceptable.
3. A recent-files list, plus a plain path field for people who want it.

Tkinter's `filedialog` is stdlib and tempting, but mixing a Tk event loop into
the server process is a portability liability; the shell-out is simpler and
degrades to option 2.

### 5.4 Live capacity panel

The single highest-value screen, because it answers the question everyone
actually has — *how many pages fit?* — before any expensive work. It runs
`collect_pages` + `plan_plate` + `validate_profiles`, all cheap (page boxes
only, no rendering), on every settings change:

```
Your document:  412 pages, US Letter, no rotation, 3 pages contain images
This plate:     3,723 usable pseudopage slots  (3,864 before reserved areas)
                → 412 pages fit on 1 plate, using 11% of it
Reduction:      109:1   ·   each page becomes 1.98 × 2.56 mm
Smallest text:  8 pt in your document → ~1.8 µm strokes on the plate
                Your writer's floor is 1.0 µm and your microscope resolves
                ~1.4 µm — this should be readable. Measured checks after
                the build are the real answer.
⚠ 2 warnings — see details
```

Every number here already exists in `plan.summary()` and
`ValidationResult.info`; the work is translation, not computation. Note the
last sentence: the closed-form legibility check is a heuristic (finding 2 in
the plan review) and the UI must say so where the number appears, not in a
footnote.

### 5.5 Progress, and the code change it requires

`build_job` currently runs to completion with no instrumentation, and a
400-page build is tens of minutes of apparent hang — unacceptable in a GUI.
Minimal, non-invasive change:

```python
# stele/build.py
def build_job(manifest_path, verify=True, preview=True, progress=None):
    """progress(stage, done, total, detail) — optional; called between units of
    work. Raises Cancelled from within if the caller's callback requests stop."""
```

Call sites, all at existing loop boundaries: per input PDF during ingest, per
placement in the materialization loop, at GDS write, per page in
`verify_plate`, at preview render. `_build_one_plate` and `verify_plate` take
the same optional parameter and pass it down. The callback returning
`"cancel"` raises a `Cancelled` exception at the next boundary, which
`runner.py` catches — cooperative cancellation, no thread killing, partial
outputs cleaned up. Default `None` keeps the CLI and every test byte-identical
in behaviour.

The UI renders: current stage, page *n* of *m*, elapsed, revised estimate, and
a live log pane (collapsed by default; the CLI's existing lines are good
material). Cancel is always available.

### 5.6 Estimates and resource guardrails

This is blocker 9, the one where a UI can do actual harm. The July 2026 memory
pass reduced the dense one-page verified probe from 7.6–7.9 GB / 125 s to
2.60 GB / 4.7 s and bounded a 120-page streamed build at 0.61 GB. Full-plate
verified performance is still unproven, so a GUI that starts a large job
without a calibrated warning remains unsafe.

Design:

- `estimate.py` reads a **calibration table** (`assets/calibration.json`)
  produced by a benchmark script over the synthetic corpus — pages × DPI ×
  image_mode × verify → seconds/page and peak RSS/page. Committed with the
  machine and stele version it was measured on. **No hardcoded guesses**: if
  no calibration row matches, the UI says "unknown" rather than inventing a
  number, in the same spirit as `unverified`.
- Before Build, show predicted time, peak memory, and output size against the
  machine's actual RAM and free disk (from `doctor`).
- Over ~60% of physical RAM: a blocking confirmation naming the number, with
  the concrete mitigations — fewer pages per run, lower DPI, `verify` off
  (with its consequence stated: the result is `unverified`, never a pass).
- Over free disk: refuse, with the shortfall in GB.
- Recommend, and default to, a **1-page trial build** before a long run. Cheap,
  and it catches almost every settings mistake.
- Post-run, append the *measured* time and RSS to the local calibration store
  so estimates improve on that machine.

### 5.7 Results

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚠  Built with warnings                          STELE-0001 · 1 plate│
│                                                                      │
│  Every page is present and correct.                                  │
│  Two things need your attention before a mask shop sees this:        │
│                                                                      │
│  • 3 pages have strokes thinner than your writer can print (0.8 µm   │
│    vs 1.0 µm minimum). They may come out broken.                     │
│      → raise the tier scale, or ask the shop about a finer writer     │
│      Pages 41, 88, 203 — view                                        │
│  • 1 image region was too small to check its shading.                │
│      → this region is unverified; nothing failed, nothing was proved  │
│                                                                      │
│  This is a software-verified candidate handoff, not fab-ready output. │
│  [what that means]                                                   │
└──────────────────────────────────────────────────────────────────────┘

  [plate preview, zoomable]     [look through the microscope]
  [Open run folder]  [Save GDS…]  [Save report…]  [Copy CLI command]
```

Components:

- **Verdict card** driven strictly by `verify.gates.status`, with the four
  states given distinct colour *and* distinct wording — never colour alone
  (accessibility, and colour is the first thing lost in a screenshot).
- **Findings list** from `verdict.py`, one entry per non-clean gate, each with
  what it means, which pages, and what to do. Table in §7.
- **Plate preview** — the existing `preview.png`, pan/zoom in canvas, with
  pseudopage grid and page numbers overlaid from `report["placements"]`, so
  clicking a cell jumps to that page.
- **Microscope view** — `stele simulate` behind a button, on the page you
  clicked, with a magnification slider mapped to `tier_scales`. This is the
  demo that makes the project legible to persona 2; it should be two clicks
  from launch, not a CLI invocation with `--cell PAGE_0000`.
- **Defect heatmaps** shown inline when present (`verify.pages.*.heatmap`).
- **"Copy CLI command"** — the exact `stele build …` line for this run.
  Retires blocker 6 by making the UI a teacher rather than a wall, and keeps
  every UI result reproducible by hand.
- **KLayout**: offer to open the GDS if the desktop app was detected; if not,
  a link and a one-line explanation of what it is — never a prerequisite.
  Blocker 11 becomes optional rather than load-bearing.

### 5.8 Wireframe: main screen

```
┌─ stele ────────────────────────────────── ⓘ doctor ─ ? help ─────────┐
│                                                                      │
│  1. Documents                                                        │
│     ┌────────────────────────────────────────────────────────┐       │
│     │  Drop PDFs here, or  [Choose files…]                    │       │
│     │  ✓ patent.pdf — 28 pages, Letter                        │       │
│     │     pages: [all          ]                              │       │
│     └────────────────────────────────────────────────────────┘       │
│                                                                      │
│  2. Plate                                                            │
│     Preset  [ 6-inch plate · standard microscope        ▾ ]  [ⓘ]     │
│     Name    [ STELE-0001            ]                                │
│                                                                      │
│     ▸ Standard settings   (12)                                       │
│     ▸ All settings        (57)   [ view as YAML ]                    │
│                                                                      │
│  3. What you'll get                        ┌─────────────────────┐   │
│     28 pages → 1 plate, 1% used            │                     │   │
│     Reduction 109:1 · 1.98 × 2.56 mm each  │   [live plate       │   │
│     Est. 4 min · 2.1 GB RAM · 40 MB GDS    │    layout sketch]   │   │
│     ⚠ 1 warning ▸                          │                     │   │
│                                            └─────────────────────┘   │
│                                                                      │
│     [ Trial: build 1 page ]        [ Build the plate ]               │
│                                                                      │
│  Software-verified candidate handoff — not fab-ready. AGPL-3.0.      │
└──────────────────────────────────────────────────────────────────────┘
```

### 5.9 Turning verification off — decided: yes, Expert only

`--no-verify` roughly halves build time and is genuinely useful while iterating
on settings. It is exposed, under these conditions:

- **Expert panel only.** It never appears in Simple or Standard, so nobody
  reaches it without having gone looking.
- **Consequence stated at the point of choice**, not in help text: *"The plate
  will be built but nothing will be checked. The result is marked* unverified —
  *that is not a pass. Nothing was proved about whether your pages survived."*
- **Stamped on the outcome.** The result screen uses the `unverified` verdict
  card (◻, distinct wording, never green), the run folder is named
  `…-unverified`, and the handoff bundle (§5.10) refuses to build from an
  unverified run without a second explicit confirmation.
- **One-click remedy.** A "Verify this now" button on the result screen runs
  `stele verify` against the existing GDS, so an unverified run is a state you
  can leave, not a dead end.

This costs nothing beyond wiring, because `build.py` already models the state
correctly: `aggregate_status` returns `unverified` rather than synthesizing a
pass, and the CLI already prints `UNVERIFIED`. The UI's job is to not be the
weak link in a chain that is already honest.

### 5.10 The mask-shop handoff bundle — decided: yes, in scope

Persona 3 is a real user with a real interface today (a multi-megabyte JSON
file), and the bundle is mostly repackaging artifacts that already exist. One
button, "Prepare mask-shop package", producing
`~/.stele/runs/<id>/handoff-<plate_name>.zip`:

```
handoff-STELE-0001/
  README.txt              what this is, what was checked, what was NOT
  STELE-0001.gds          the mask layout (one per plate for a set)
  summary.html            the §5.7 verdict and findings, self-contained, printable
  report.json             the full machine-readable report, unchanged
  preview.png             full-plate overview
  microscope.png          simulated through-the-reader view of one page
  config/                 manifest.yaml + the four profile YAMLs, as built
  RFQ.md                  the vendor checklist, PRE-FILLED (below)
  SHA256SUMS              every file above
```

The part that is more than a zip is **`RFQ.md`**: the ten-question checklist
from `docs/physical-validation.md` §2, with every answer this build *assumed*
already filled in from the report, so the shop corrects a concrete number
instead of answering an open question. From `config` + `report`:

| RFQ question | Pre-filled from |
|---|---|
| Format, record limits, vertex ceiling | `fab.max_vertices_per_polygon` (4000), `gds.size_mb`, `max_file_size_mb` |
| Layer/datatype map and tone convention | `fab.layer`/`datatype`, plus `orientation.fab_tone_assumption` verbatim — *positive-tone assumed; please confirm or correct* |
| Min feature / space / process bias | `fab.min_feature_um`, `min_space_um`, `process_bias_um` (0), **and the measured stroke-width distribution from `verify.pages.*.stroke_widths_um`** — what the plate actually contains, not just what was requested |
| Grid / address unit | `fab.dbu_nm` (1 nm) |
| Hierarchy, cell and polygon counts | `gds.cells`, `polygons`, `references`, `max_cells`, and the halftone tile-cell count |
| Density rules | `fab.density_min`/`max` and the **measured** plate density from `drc_plate.density` |
| Inspection policy | the dense-text/dither pattern classes actually present, with the note that this is the question most likely to carry a cost |
| Blank spec, required marks, price tiers | carried over as questions — stele has no view on these |

Two guardrails, both load-bearing:

1. **`README.txt` leads with the candidate-handoff statement and the
   `UNENFORCED_FAB_FIELDS` list**, verbatim from the report. The bundle's
   purpose is to start an informed vendor conversation, and it must never read
   as a claim that the plate is ready to write.
2. **Non-clean status is on the front page.** A `pass_with_warnings` bundle
   states the warnings in `README.txt`, not only inside `summary.html`. An
   `unverified` run requires a second confirmation to bundle at all, and the
   zip is named `…-UNVERIFIED.zip`.

Effort is small — `summary.html` is the §5.7 result screen rendered to a static
file, and the RFQ fill is a template over the report — and it closes the loop
from `docs/physical-validation.md`, which currently describes a checklist a
human has to assemble by hand.

---

## 6. The plain-language layer

The actual product for personas 1 and 2. Every field gets a label, a unit, one
sentence of help, and its provenance marker (**P** = patent example,
**A** = engineering assumption, from the YAML comments). Abridged below —
`labels.py` carries the full 57, and CI enforces completeness.

### Documents (`InputSpec`, `OutputSpec`)

| Field | Label | Help |
|---|---|---|
| `inputs[].path` | Document | The PDF to put on the plate. Add several to combine them. |
| `inputs[].pages` | Pages | `all`, or ranges like `1-5, 8`. |
| `output.plate_name` | Plate name | Etched large enough to read without a microscope. Plate sets get "1/3" appended. |
| `output.gds` | Save mask file as | The manufacturing file (GDSII) a mask shop needs. |
| `output.report` / `preview` | — | Managed by the UI inside the run folder. |

### Plate & manufacturing (`FabProfile`)

| Field | Label | Help | Prov |
|---|---|---|---|
| `name` | Writer profile name | Which mask writer's rules these are. | |
| `plate_width_mm` / `plate_height_mm` | Plate size | Glass blank dimensions. 152.4 mm = the standard 6-inch photomask. | A |
| `edge_exclusion_mm` | Unusable border | Kept clear for handling and edge chips; nothing is placed here. | P |
| `min_feature_um` | Thinnest line the writer can make | The single most important number. Text thinner than this may print broken. | A |
| `min_space_um` | Smallest gap the writer can keep | Below this, separate shapes merge. | A |
| `process_bias_um` | Etch allowance | How much the etch grows or shrinks shapes. Your shop states it; 0 until they do. | A |
| `dbu_nm` | Coordinate grid | All geometry snaps to this. 1 nm is standard; leave it alone. | |
| `layer` / `datatype` | Layer / datatype number | Which GDSII layer the chrome pattern goes on. Your shop specifies these. | |
| `max_vertices_per_polygon` | Max corners per shape | 4000 keeps each record inside a limit some fab tools have. Don't raise it without a reason. | |
| `max_file_size_mb`, `max_cells` | Size limits | Build fails the budget check above these. | |
| `density_min` / `density_max` | Allowed ink coverage | Some processes reject plates that are too empty or too full. | |
| `drc_gate` | If shapes break the writer's rules | **Warn** — finish and report (result can never be a clean pass) · **Stop** — fail the build. | |
| `notes` | Notes | Free text, copied into the report. | |

### Microscope (`ReaderProfile`)

| Field | Label | Help | Prov |
|---|---|---|---|
| `magnification` | Magnification | The bundled microscope's power. Doesn't change the plate — changes whether it can be read. | A |
| `numerical_aperture` | Lens light-gathering (NA) | The real limit on how small readable text can be. Higher resolves finer, costs more. | A |
| `wavelength_nm` | Light colour | Nanometres. 580 = yellow-green. Shorter light resolves finer detail. | P (band) / A (580) |
| `contrast_criterion` | Minimum readable contrast | How much darker a stroke must be than its background to count as legible (Michelson, 0–1). | A |

### Content (`ContentPolicy`)

| Field | Label | Help |
|---|---|---|
| `dpi` | Scan detail | How finely each page is read before conversion. 900 is a good balance; higher is slower and needs much more memory. |
| `threshold` / `fixed_threshold` | Black-and-white conversion | **Fixed** cutoff, or **automatic** (Otsu) per page. Automatic suits scans; fixed suits clean digital documents. |
| `fit_mode` | Mixed page sizes | **Fit** — scale each page to the pseudopage box · **Strict** — refuse anything that isn't US Letter. |
| `expected_min_text_pt` | Smallest text in your document | Point size. Feeds an early warning only; the real check is measured after the build. |
| `tag_images` | Find image areas | Detect photo regions separately from text. |
| `image_mode` | Photos and shading | **Threshold** — pure black and white (simple, loses shading) · **Halftone** — tiny dots that reproduce grey (recommended for photos). |
| `dither_pitch_um` | Halftone dot spacing | 0 = automatic, matched to your writer's limits so the result is manufacturable by construction. |
| `dither_mask` | Halftone pattern | **Blue noise** — random-looking, no visible pattern (recommended) · **Clustered** — traditional print-style dots. |
| `max_exact_tiles_per_plate` | Halftone detail budget | Each unique dot pattern costs a cell in the file. Past this, patterns fall back to a fixed library — still verified, slightly coarser. |
| `color_mode` | Colour | **Single colour** · **RGB triads** for the sunlight projector (requires halftone images). |
| `channel_transforms` | Colour channel alignment | Per-channel shift and scale for the projector's optics. Advanced. |
| `unsupported_features` | Unusual PDF content | Currently everything is rendered as an image; kept for the future vector path. |

### Layout (`LayoutSpec`)

| Field | Label | Help | Prov |
|---|---|---|---|
| `pseudopage_width_um` / `height_um` | Size of one page on the plate | Micrometres. 1980 × 2560 µm ≈ 2 × 2.5 mm — the patent's example, ~109:1 reduction. | P |
| `pitch_x_um` / `pitch_y_um` | Spacing between pages | Must be at least the page size; the difference is the gutter. | A |
| `title_text` | Title on the plate | Large etched text. Blank uses the plate name. | |
| `title_height_um` | Title size | 2500 µm = 2.5 mm, readable with the naked eye. | P |
| `title_band_um` | Title strip height | Reserved at the top; no pages placed there. | |
| `nav_band_um` | Guide strip at the bottom | 0 = off. Adds a page map and scale bar so a finder can navigate the plate. | |
| `fiducial_size_um` | Alignment crosses | Corner marks for measurement and alignment. | |
| `orientation_glyph_um` | Orientation mark | An "F" — asymmetric, so a mirrored plate is detectable and machine-checked. | |
| `mirrored` | Mirror the whole plate | For writing on the far side of the glass. Verified against the actual file, never assumed. | |
| `polarity` | Chrome or clear | Only clear-field is implemented today. | |
| `tier_scales` | Magnification ladder | e.g. `[4, 1]` places every page twice — once big enough to hint at with a loupe, once at full reduction. The patent's "lead the finder to magnify" idea. | P |

---

## 7. Translating the verdict

`verdict.py` maps gates to findings. Status wording first:

| `gates.status` | Headline | Meaning |
|---|---|---|
| `pass` | ✅ **Built and verified** | Every check passed. Still a candidate handoff, not fab-ready. |
| `pass_with_warnings` | ⚠ **Built with warnings** | Content is correct; something about manufacturability or readability needs attention. |
| `fail` | ❌ **Failed verification** | Something is wrong with the output. Do not send this to a mask shop. |
| `unverified` | ◻ **Built, not checked** | You turned verification off. Nothing was proved — this is not a pass. |

Per-gate findings:

| Gate | Plain language | Suggested action |
|---|---|---|
| `worst_defect_mismatch` > 2% | "Page *N* doesn't match the original closely enough." | Open the heatmap; check unusual fonts or transparency. |
| `structural_defects` > 0 | "*k* pieces of content are missing or extra — a dropped glyph or a phantom blob, not edge fuzz." | Serious. Heatmap, then report it. |
| `component_failures` > 0 | "*k* shapes don't match the original's shape closely enough." | Heatmap. |
| `placements_found ≠ expected` | "Expected *n* pages on the plate, found *m*." | Build bug — report it. |
| `chirality` false | "The plate's handedness doesn't match what you asked for — it may be mirrored." | Check the Mirror setting. |
| `stroke_floor_failing_pages` | "*k* pages have strokes thinner than the writer can print (0.8 µm vs 1.0 µm)." | Raise tier scale, or a finer writer. Names the pages. |
| `width_violations` / `space_violations` | "*k* places break the writer's minimum width/gap rules." | Coarser halftone pitch, or confirm the rules with your shop. |
| `readability_failing_tiers` | "At ×1, simulated text contrast is below the readable threshold — this tier may not be legible through the bundled microscope." | Add a larger tier; larger text; higher-NA reader. |
| `image_tone_unassessed_regions` | "*k* image regions were too small to check shading. Not a failure — *unverified*." | Larger regions, or accept it explicitly. |
| `density.pass` false | "Ink coverage is outside what your process allows." | Fewer pages per plate, or confirm density rules. |
| `budget.*` false | "The file exceeds a limit (size / cells / corners per shape)." | Split across plates; check limits with the shop. |
| `drc_severity: warn` | Always shown when geometry gates are non-clean: "these are warnings because your writer profile says warn — with **Stop** selected, this build would have failed." | |
| `UNENFORCED_FAB_FIELDS` | An always-present "what was *not* checked" panel, verbatim from the report. | |

That last row matters most. The plan's discipline is that the report never
implies more coverage than it has; the UI must carry an explicit
**"what wasn't checked"** section, not just a list of passes.

---

## 8. Implementation plan

Sequenced so each phase is independently useful and shippable.

**Shipped in v0.2:** progress/cancellation instrumentation; source-bundle
installers and launchers; `stele doctor`; the token/origin/host/CSP-protected
local server; PDF upload and inspection; capacity calculation; reproducible run
emission; the simple settings path; one-page trial and full builds;
plain-language verdicts; previews and downloads; release CI and packaged-data
checks.

**Next, based on user evidence:** calibrated resource estimates, run history,
native open-folder integration, microscope simulation, and the mask-shop
handoff package. The all-fields editor stays deferred unless users demonstrate
that YAML plus the CLI is inadequate for vendor-profile work.

**Phase 0 — instrumentation (≈1 day).** `progress=None` + `Cancelled` through
`build_job` / `_build_one_plate` / `verify_plate`. Test: a build with a
recording callback emits a monotonic sequence and byte-identical GDS to one
without.

**Phase 1 — install (≈2–3 days).** `install.sh`, `install.ps1`, `--offline`,
launchers, `stele doctor`, `stele uninstall`/`update`. Docs: a
non-programmer-facing `docs/install.md` and a README section. Test: clean VM
per supported row in §4.2, plus a networking-disabled run.

**Phase 2 — headless UI core (≈3–4 days).** `server.py` (token, Origin check,
CSP, artifact sandbox), `schema.py`, `labels.py` + the completeness test,
`runs.py`, `/api/inspect`, `/api/calc`, `/api/presets`. Tested entirely via
`http.client` against a live server — no browser needed in CI.

**Phase 3 — the page: simple path (≈4–5 days).** `app.html`, Simple + Standard
panels, live capacity panel, trial build, SSE progress, verdict card,
`verdict.py`, preview, downloads, Copy CLI command.

**Phase 4 — Expert panel (≈3 days).** Generated form over all 57 fields, YAML
side-by-side with live pydantic validation, `channel_transforms` editor,
preset save/load into `~/.stele/profiles`, and the verification toggle with its
`unverified` handling and "Verify this now" remedy (§5.9).

**Phase 5 — estimates and guardrails (≈2–3 days).** Benchmark script,
`assets/calibration.json`, `estimate.py`, confirmations, post-run measurement
feedback.

**Phase 6 — microscope and navigation (≈2 days).** Clickable plate preview,
`/api/simulate`, magnification slider, heatmaps inline, optional KLayout
launch.

**Phase 7 — mask-shop handoff bundle (≈2 days).** `bundle.py`: `summary.html`
rendered from the run's report, pre-filled `RFQ.md` template over
`docs/physical-validation.md` §2, `SHA256SUMS`, the `README.txt` disclosure
block, and the non-clean/unverified naming and confirmation rules (§5.10).
Test: bundle a known-`pass_with_warnings` golden run and assert the warnings
and the `UNENFORCED_FAB_FIELDS` list appear in `README.txt`, and that an
unverified run produces a `…-UNVERIFIED.zip`.

**Phase 8 — polish (≈2–3 days).** Keyboard navigation and focus order, ARIA
labels, colour-plus-wording for every state, contrast check, prefers-color-scheme,
help text review with someone from persona 1, `docs/usability.md` updated to
reflect what shipped.

Roughly **3–4 weeks** at the plan's ~50%-time assumption. Phases 0–3 alone
(~2 weeks) already retire blockers 1–8 and 10.

Phase 7 is deliberately last among the feature phases: it consumes the report
and the result screen, so it is cheapest once both are settled, and it is the
one phase that produces something a third party reads.

### Testing

- Server tests via `http.client`: every endpoint, auth rejection, Origin
  rejection, path-escape attempts on `/api/artifact`, cancel mid-build.
- **Schema/label completeness test** — every pydantic field has label, help,
  unit, provenance. This is the anti-drift mechanism.
- **Round-trip test** — settings → emitted manifest+profiles → `load_manifest`
  → identical resolved config. Guarantees UI runs are CLI-reproducible.
- Verdict-mapping tests over synthetic `gates` dicts, including every planted
  defect class from `tests/golden/test_planted_defects.py`, asserting each
  produces a finding with an action.
- `doctor` tests with a deliberately broken import.
- Golden HTML test: the page renders with no external requests (parse for
  `http://`, `https://`, `//`).
- **Usability acceptance**: a non-programmer, given only a download link, gets
  a verified plate from a PDF of their choosing, unaided, in under 15 minutes,
  and can afterwards say what the verdict meant and what "candidate handoff"
  implies. If they cannot, the UI has failed regardless of test coverage.

---

## 9. Risks, and the decisions taken

| Risk | Mitigation |
|---|---|
| **A GUI makes overclaiming easy** — a green tick erases the candidate-handoff distinction the project is built on. | §3 guardrails; verdict wording driven only by `gates.status`; permanent handoff banner; explicit "what wasn't checked" panel; §8's usability test asks the user to *explain* the verdict. |
| **Non-programmers exhaust RAM** on realistic corpora (memory cliffs reduced, but full-plate verification remains unproven). | §5.6: calibration-based estimates, "unknown" rather than a guess, blocking confirmation, trial build as the default path. |
| **UI drifts from the profile models.** | Generated schema + enforced label completeness in CI. |
| **Maintenance burden on a solo project.** | Stdlib only; no build step; ~1,500 lines; the UI touches `build.py` in exactly one place (the progress parameter). |
| **Security of a local build-running server.** | Token, Origin check, CSP, artifact sandbox, ephemeral port, idle exit (§5.1). |
| **Installer rot** (uv API, wheel availability, OS versions). | `uv sync --frozen` against the committed lock; `doctor` as the canary; explicit refusal on unsupported platforms rather than a doomed compile. |
| **The handoff bundle overstates readiness** to the one reader who could act on it. | §5.10: `README.txt` leads with the candidate-handoff statement and `UNENFORCED_FAB_FIELDS`; warnings on the front page; unverified runs bundle only after a second confirmation and are named `…-UNVERIFIED.zip`. |

**Decided (2026-07-26):**

- **A UI is in scope.** The plan's "GUI — out of scope" line is superseded;
  M8 — operability is the work described here.
- **Verification can be switched off, in the Expert panel only**, with the
  consequence stated at the point of choice, the `unverified` verdict stamped
  on the result and the run folder, and a one-click "Verify this now" remedy
  (§5.9).
- **The mask-shop handoff bundle ships**, as Phase 7, with a pre-filled RFQ
  derived from the build's own measured numbers (§5.10).

Still open, and cheap to defer:

| Open | Position |
|---|---|
| **Localisation.** A preservation tool has a plausible non-English audience. | Keep all user-facing strings in `labels.py`/`verdict.py` so translation is a data change, not a code change. Ship English only; revisit on demand. |
| **Whether `install.sh` is served from a domain or only from the repo.** A `curl \| sh` one-liner needs a URL someone trusts. | Repo-hosted raw URL to start; a custom domain only if there is a signed-release story to go with it. |

---

## 10. Recommendation

Release v0.2 to a small set of archivists and demonstrators before adding
another configuration surface. Observe whether they can get from the release
ZIP to a one-page verified result without help, whether the trial duration is
acceptable, and whether they can explain both the verdict and “candidate
handoff.” Those are the product gates.

Then implement calibrated memory/time estimates and run history first: they
make repeated and larger jobs safer. Add the microscope view next because it
has high demonstration value. Build the vendor handoff package with a real
mask-shop contact, so its RFQ language reflects an actual intake workflow
rather than an internally plausible template.
