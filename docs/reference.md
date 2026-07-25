# stele reference: manifest, profiles, commands, report

## Commands

```sh
stele validate job.yaml    # cross-profile checks (errors gate; legibility check WARNS)
stele calc job.yaml        # capacity/resolution math, reserved regions, usable slots
stele build job.yaml       # compile -> GDS (per plate) + preview PNG + report JSON
stele verify job.yaml      # re-verify an existing GDS (--gds to point elsewhere)
stele simulate job.yaml    # through-the-reader view of a page region (--cell, --region)
```

Build exit codes: 0 = pass, pass_with_warnings, or unverified (`--no-verify`
prints "UNVERIFIED" — output built without gates is never reported as a
pass); 1 = fail; 2 = overflow. For multi-plate sets the exit code comes from
the AGGREGATE `plate_set.status` — any failing plate fails the set — and
`verify`/`simulate` operate over every `.pNN` member. `simulate` exits 1
when no stroke bin meets the reader's contrast criterion.

## Job manifest

```yaml
inputs:
  - path: ../corpus/book.pdf     # relative to the manifest file
    pages: all                   # or "1-5,8" (1-based)
profiles:
  fab: ../profiles/fab/generic-laserwriter.yaml
  reader: ../profiles/reader/na025-580nm.yaml
  content: ../profiles/content/default.yaml
  layout: ../profiles/layout/pseudopage-6in.yaml
output:
  gds: ../out/plate.gds          # multi-plate sets get .p01/.p02... suffixes
  plate_name: STELE-0001
  report: ""                     # default <gds>.report.json
  preview: ""                    # default <gds>.preview.png
```

## Profiles (ship with provenance comments: patent example vs assumption)

**fab** — the vendor contract (unconfirmed = candidate handoff, `drc_gate: warn`):
`plate_width_mm/plate_height_mm`, `edge_exclusion_mm`, `min_feature_um`,
`min_space_um`, `process_bias_um`, `dbu_nm`, `layer/datatype`,
`max_vertices_per_polygon` (4000 default: GDS records stay signed-16-bit
safe), `max_file_size_mb`, `max_cells`, `density_min/max`,
`drc_gate: warn|fail` (warn -> geometry failures surface as
pass_with_warnings, never an unqualified pass).

**reader** — the bundled optics (the binding constraint on text size):
`magnification`, `numerical_aperture`, `wavelength_nm`,
`contrast_criterion` (Michelson gate for the readability sim).

**content** — ingest + tone policy:
`dpi` (900), `threshold: fixed|otsu`, `fixed_threshold`,
`fit_mode: fit|strict`, `unsupported_features: raster|error` (placeholder
until the vector frontend), `expected_min_text_pt` (closed-form WARNING
only), `tag_images`, `image_mode: threshold|dither`,
`dither_pitch_um` (0 = auto: max(min_feature, min_space) — DRC-clean by
construction), `dither_mask: bluenoise|clustered`,
`max_exact_tiles_per_plate` (20000 default: unique exact halftone patterns
are one GDS cell each, so the worst case is the 1,040-cell tone library +
this cap; past it, tiles degrade deterministically to quantized-tone
patterns, still tone-verified), `color_mode: mono|rgb_triad` (projector
route; needs image_mode: dither, enforced),
`channel_transforms: {R: {dx_um, dy_um, scale, rotation_deg}, ...}` —
validated: channels R/G/B only, scale in (0, 2], finite values. The
implemented contract is translation + scale applied to geometry; rotation
is recorded for the projector optics only (rotating dither geometry would
break DRC-cleanliness-by-construction). Sub-images must stay inside the
page frame (build error otherwise), and the occupancy audit independently
re-checks every read-back page instance against its planned slot block.
Anaglyph 3-D is deferred: it needs authored stereo pairs (left->R,
right->B, G blanked); the triad machinery carries it once such inputs exist.

**layout** — plate furniture and tiling:
`pseudopage_width_um/height_um` (patent example 1980x2560),
`pitch_x_um/pitch_y_um`, `title_text` (default plate name; plate sets get
"k/n" appended), `title_height_um` (2.5 mm naked-eye tier),
`title_band_um`, `nav_band_um` (0 = off; page map + scale bar),
`fiducial_size_um`, `orientation_glyph_um`, `mirrored`,
`polarity: clear_field`, `tier_scales: [1.0]` (e.g. [4.0, 1.0]: every page
placed once per scale, largest first — the patent's magnification ladder).

## Report (per plate; single-plate keys aliased at top level)

- `plan`: reserved rects, theoretical vs usable slots, plate k of n, tiers.
- `placements`: cell, page_key, slot, origin, scale, tier_scale.
- `ingest`: per page — boxes, rotation, sha256.
- `halftone`: pitch, mask + asset hash, tile cells, per-page sites/patches,
  triad channels + transforms.
- `verify.pages.<CELL>`: raw/defect mismatch, structural defects, component
  failures, stroke-width distribution + writer-floor gate, klayout DRC
  (dithered pages: direct+boundary-bands mode), image tone (per channel for
  triads), heatmap path when failing.
- `verify.readability`: per-tier SIMULATED reader verdict — stroke-contrast
  bins from the densest text window (image rects excluded), Michelson vs the
  reader criterion; per-channel (R/G/B filter bands) for triad jobs. A
  failing tier, like unassessed image tone, blocks an unqualified pass.
- `verify.pages.<CELL>.reference_threshold`: the binarization policy applied
  to the reference render — `otsu` builds are verified against an
  otsu-derived reference threshold, not a fixed one.
- `verify.gates`: content_pass (binds always: XOR defects, component
  coverage, shape, placement audit, chirality, occupancy incl. per-instance
  slot containment, tone), geometry_pass (vendor rules: stroke floor,
  width/space, budgets, density), readability_failing_tiers and
  image_tone_unassessed_regions (warning-level: cap status at
  pass_with_warnings), `status: pass | pass_with_warnings | fail`;
  plate sets aggregate to `plate_set.status` (+ `unverified`).
- `orientation`: tone truth table + fab tone assumption; chirality result
  includes the transform READ FROM THE GDS.
- provenance: input/profile/output hashes, dependency versions, resolved
  config, unenforced-field disclosure.

## Verification model (why you can trust a green build)

Three independent codebases: gdstk writes; klayout reads/renders back;
pypdfium2 renders the reference (the build renders with PyMuPDF). Band
tolerances are asymmetric and scale-aware (missing ink gated at 1 build
pixel; phantom ink +1 — engines disagree on stem widths by ~1 um even with
embedded fonts, measured). Tone hysteresis spans engine gray divergence
(±8 gray text, wider inside images, envelope targets for dither). Every
threshold is planted-defect calibrated: dropped polygon, dropped region,
phantom blob, shifted text, missing 1 um stroke, severe thinning, reserved
intrusion — each must be caught for the suite to pass.
