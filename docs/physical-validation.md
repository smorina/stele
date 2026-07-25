# Physical validation path (deferred; software-only per project decision)

Everything stele produces today is a **software-verified candidate handoff**.
Turning that into "fab-ready" requires exactly two artifacts, both documented
here so the step is a purchase order away, not a design effort.

## 1. The calibration plate

The first physical artifact should never be a content plate. The calibration
job (~1 day to implement on the existing layout engine) fills one 6″ plate
with instruments, each answering one question:

| feature | question it answers |
|---|---|
| Line-pair ladders, H+V, 0.8/1.0/1.2/1.4/1.6/2.0/3.0/4.0 µm | where the writer's real floor is (brackets our assumed 1 µm) and whether it is isotropic |
| Text-tier ladder: the same pangram at 72→4 pt equivalent (≈150 µm→8 µm char height), deliberately extending BELOW the computed legibility floor | the EMPIRICAL legibility floor through the actual reader optics, vs our Rayleigh estimate (1.42 µm strokes at NA 0.25 / 580 nm) |
| Dither swatches: 11-step ramps, blue-noise and clustered, at 1/2/3 µm pitch | dot gain (process bias) per pitch; tone-transfer curve to load into `process_bias_um` and a future tone LUT |
| Siemens star + checkerboards | astigmatism/orientation-dependent resolution |
| Orientation "F" glyphs in all four chiralities | that our chirality convention survives the fab's data path |
| 10–90% density blocks | the writer/etch density limits for `density_min/max` |
| Corner-contact coupons (pre-repair dither samples) | whether the fab's inspection flags checkerboard contacts (we repair them anyway; this prices the requirement) |
| Fiducials + plate ID + scale bars | registration and a sanity anchor for microscope measurements |

Acceptance protocol for the returned plate: measure each ladder under a
calibrated microscope; record the smallest resolved line pair, the smallest
legible text tier through the ACTUAL bundled reader design, and the measured
vs nominal coverage per dither swatch. Feed results back into the fab profile
(`min_feature_um`, `process_bias_um`, density bounds) and the reader profile
(`contrast_criterion` calibration); rebuild; diff the verification reports.

## 2. The vendor rule deck (RFQ checklist)

Questions to put to 2–3 mask shops with the M1 sample GDS attached
(`out/patent_demo.gds` — 28 pages of dense text is exactly the unusual
pattern class they should react to):

1. Accepted formats and flavors: GDSII record-size limits (we cap XY records
   under 0x7FFF bytes — vertex ceiling 4,000), OASIS?
2. Layer/datatype map and tone convention: is drawn polygon = chrome retained
   (our assumption: positive-tone, clear-field) or inverse?
3. Minimum feature and space, isolated vs dense, and PROCESS BIAS (etch) —
   number, not adjective.
4. Grid: address unit; is 1 nm DBU acceptable or do they snap?
5. Hierarchy: SREF/AREF depth limits, cell-count limits, per-cell polygon
   limits, total file-size limits. (A dithered plate carries ~10^3 tile cells
   × ~10^4 refs/page.)
6. Density rules: min/max pattern density, window size.
7. Inspection: can their defect inspection handle full-plate dense text and
   dither fields, or must inspection be waived/limited? Cost either way.
8. Blank spec: chrome-on-soda-lime vs chrome-on-quartz; for the archive use
   case ask about low-defect blanks and pellicle-free handling. For
   5,000-year durability, ask about etched-quartz alternatives (patterning
   INTO the glass rather than a chrome film) and AR-coating-free chrome.
9. Required marks: barcodes, titles, alignment marks they insert — where, and
   how to reserve for them.
10. Price tiers vs feature size (a 2 µm-floor laser-written plate is a
    commodity; sub-µm e-beam is not) and turnaround.

Encode every answer in a named fab profile (e.g. `profiles/fab/<vendor>.yaml`)
with `# source: vendor deck <date>` provenance comments, set `drc_gate: fail`,
rebuild, and require `status: pass`.

## 3. What changes in stele when this happens

- `fab` profile: real numbers replace assumptions; `drc_gate: fail`.
- Verification gains a tone-transfer LUT (measured dot gain) applied at
  dither time; `process_bias_um` participates in the stroke-floor gate (it
  already does — it is just zero today).
- The report's "software-verified candidate handoff" language flips to
  "verified against <vendor> deck <date> + coupon <id>".
