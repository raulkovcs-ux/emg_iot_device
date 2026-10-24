# 8. Wearing the band and running a session

This answers the review-sheet item "MVC measurement procedure and
anatomical landmarks for armband placement described". Print it as the
session checklist.

## 8.1 Before the subject arrives

- [ ] Ethics approval and consent forms ready (`06_safety_and_ethics.md`)
- [ ] Band charged **and USB unplugged**; power switch ON; blue LED blinking (Wi-Fi up, waiting for host)
- [ ] microSD card inserted with `fama_net.bin` (only if on-device inference is part of the session)
- [ ] Electrodes cleaned with 70 % isopropanol, dry
- [ ] Host laptop on the lab Wi-Fi; `python -m famaband.record --seconds 5` works
- [ ] Hand dynamometer or load cell connected (for MVC and target force), if used

## 8.2 Placement (same every session)

1. **Arm posture:** seated, elbow at 90°, forearm resting on the table
   in neutral rotation (thumb up), wrist relaxed.
2. **Landmarks:** mark the medial epicondyle (elbow) and the ulnar
   styloid (wrist) with a skin marker. Measure the distance between
   them, then mark the point **1/3 of that distance distal to the
   epicondyle**. That is the band's centre line, over the bulk of the
   forearm flexors and extensors and similar to the NinaPro DB2 ring
   placement.
3. **Skin:** if the skin is hairy, shave it; wipe with water or alcohol
   and let it dry. No gel is needed for dry electrodes.
4. **Orientation:** wrap the strap with the hub on the dorsal side.
   Place **pod 1** (coloured stitch) over the extensor carpi radialis:
   dorsal-radial, about two finger-widths lateral to the dorsal midline.
   Pods 2–8 follow around the forearm in the ulnar direction, evenly
   spaced at about 45°. Every pod's "+" electrode points toward the
   wrist.
5. **Tension:** tight enough that no pod rocks, loose enough that two
   fingers slide under the strap.
6. **Bias electrode:** a disposable Ag/AgCl gel electrode on the
   olecranon (or the ulnar styloid), clipped to the bias lead.
7. **Photograph** the placement with a ruler in the frame and note the
   circumference and the distances in the session log. This makes day-2
   placement reproducible.
8. Wait **2–3 minutes** so the dry-electrode contact settles (sweat
   layer), then run the contact check:
   ```bash
   python -m famaband.contact
   ```
   All channels should read "ok". If one fails, reseat that pod and
   check the bias electrode.

## 8.3 Session protocol (default timings; edit them to match the methodology chapter)

```bash
python -m famaband.protocol_runner --subject S03 --day 1 [--force-serial /dev/ttyUSB0] [--config my.json]
```

| Block | Content | Markers |
|---|---|---|
| Rest baseline | 60 s relaxed, forearm supported. The device's SampEn baseline forms over the first 60 s of the session | — |
| MVC pre | 3 maximal grips × 5 s, 60 s rest between | `MVC_pre_k_START/END` |
| Cycle 1–8, G block | 8 gestures (rest, fist, open palm, pinch, wrist flexion, wrist extension, pronation, supination) × 5 repetitions, 5 s hold + 3 s rest, cued on screen | `G_<gesture>`, `G_<gesture>_END` |
| Cycle 1–8, F block | 6 isometric grips × 20 s at the target load with 10 s rest; target rises linearly from **50 % MVC (cycle 1) to 75 % MVC (cycle 8)** | `F_<pct>_START/END` |
| Between cycles | 60 s rest | `CYCLE_<n>` |
| MVC post | 3 × 5 s | `MVC_post_k_START/END`; summary JSON reports the **MVC drop %** (fatigue criterion: 20–25 %) |

The runner writes:
* `recordings/S03_D1_<time>.fbr`: the host copy of the stream, results, markers and status
* `..._summary.json`: marker timetable, MVC values, MVC drop, link statistics
* `..._force.csv`: if a force sensor is connected

The **device's microSD card** holds the lossless raw 2 kSPS log of the
same session (`S03_D1_000.fbr`). Treat the card copy as primary data.

## 8.4 After the session

1. End the session if the runner did not, or long-press the button.
2. Remove the band; inspect and photograph the skin; note any redness.
3. Power off, then copy the microSD files. Charge only now.
4. Clean the electrodes and strap.
5. Run the quality check on the new data:
   ```bash
   python -m famaband.io recordings/S03_D1_*.fbr     # lost frames, CRC errors, marker count
   ```

## 8.5 From recordings to the thesis splits

* Markers and EMG share one frame timebase, so cut the per-session
  **60 / 10 / 30 % temporal split** on frame indices. No window may cross
  a split boundary.
* `famaband.io.read_fbr` returns the raw 2 kSPS signal (re-filter it
  offline as you like) and the 1 kHz stream exactly as the device
  pipeline saw it.
* To reproduce the on-device Tier A/B behaviour offline (for the
  ablation tables), run `famaband.pipeline_ref.run(raw_volts, graph)`.
  It is the float64 reference of the float32 firmware pipeline. The tests
  show the same gesture decisions (> 99 % of windows), probabilities
  within 5·10⁻³ and Tier B updates on exactly the same windows
  (`host/tests/test_pipeline.py`).
