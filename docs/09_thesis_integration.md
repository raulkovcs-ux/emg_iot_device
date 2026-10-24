# 9. How the device closes open items in the FAMA-Net review sheet

The review sheet (7 Oct 2026, pseudo data) lists unchecked items. This
table shows which ones the device or its tooling resolves, which it only
supports, and what still has to come from the real study.

| Review-sheet item | Status with FAMA-Band | Where |
|---|---|---|
| §4 "Exact sampling rate stated (chapter gives 800–1000 Hz)" | **Resolved by design:** 1000.00 Hz ± 0.02 Hz (2.048 MHz ± 20 ppm oscillator, ADC at 2000 SPS, FIR + decimate-by-2). State it as "1000 Hz, crystal-referenced, ±20 ppm"; bring-up step 6 measures it | `03_hardware_design.md` §3.4 |
| §4 "MVC measurement procedure and anatomical landmarks for armband placement described" | **Resolved:** written procedure (1/3 epicondyle–styloid, ch1 over ECR, photo with ruler), MVC 3 × 5 s pre and post, scripted cues | `08_wearing_and_session_protocol.md` |
| §4 "DB2 channel selection justified by matching the armband's spatial layout" | **Supported:** 8 equally spaced bipolar pairs at 45°, the same ring geometry as the equally spaced DB2 electrodes, so pick 8 of the 12 DB2 channels that form the forearm ring. The argument itself goes in the thesis | `01_research.md` §1.3 |
| §1 leakage checklist "Tier B pseudo-labels and updates are shown to use only past windows, with weights reset at session end" | **Resolved for the device implementation:** causality test (perturbing future samples changes no past output) and reset test pass | `host/tests/test_pipeline.py::test_pipeline_is_causal`, `::test_session_end_restores_pretrained_weights` |
| §1 "All hyperparameters tuned on the validation slice only" | Supported: the device exposes every Tier A/B parameter in `fama_tierb_cfg_t`; markers let you cut the validation slice exactly | `05_firmware.md` §5.3 |
| §2 "Robustness grid reported (m ∈ {2,3}, r ∈ {0.15, 0.20, 0.25})" | Supported: raw data at 2 kSPS on SD, and `dsp_ref.sample_entropy(x, m, r_factor)` is the device's exact definition | `host/famaband/dsp_ref.py` |
| §2 "Label range (s_label) reported from the real dataset" | Pending real data; the device logs SampEn and its baseline in every result packet, so s_label can be plotted directly | `.fbr` result packets |
| §3 deployment metrics (latency 9 ms, Tier B 38 ms, model 0.4 MB) | **Replace the pseudo values with measured ones** from bring-up step 9. Reference model: 59.5 k parameters, 243 kB blob, about 0.9 MB PSRAM | `07_assembly_and_bringup.md` §7.3 |
| §5 "All time constants restated in seconds after the window-rate change" | Supported: the device runs 100 ms hops, so Tier A warm-up = 300 windows = 30 s; EMA α = 0.01 per window ↔ τ ≈ 10 s; Tier B history = 24 windows = 2.4 s; refractory = 300 windows = 30 s | `05_firmware.md` §5.3 |
| §5 "Accuracy on fresh data after the session-end reset shown" | Supported: reset is exact (test), so post-reset accuracy equals the pre-trained model's; measure it on day-2 data | test above |
| §6 "Public repository with code (including adapt.py) and the split definitions" | Partly: device, firmware, host tools and reference adaptation code are in this repository. Add the thesis training code (`adapt.py`) and the split definitions | this repo |
| §6 "Limitations stated" | Device-specific limitations to add: dry electrodes (contact varies, mitigated by active guarded pods), no force sensing on the band, prototype not certified to IEC 60601 | `06_safety_and_ethics.md` |
| §4 Ethics "approval number or exemption statement" | Required before first use; checklist provided | `06_safety_and_ethics.md` §6.4 |

## Suggested text for the methodology chapter (device paragraph)

> Surface EMG was recorded with FAMA-Band, a custom eight-channel armband
> built for this work. Eight active dry-electrode pods (two 10 mm ENIG
> electrodes 20 mm apart, buffered on the pod by a CMOS op-amp with a
> driven guard) were placed at equal angular spacing around the forearm,
> one third of the epicondyle–styloid distance distal to the medial
> epicondyle. Channel 1 was placed over the extensor carpi radialis. A
> right-leg-drive electrode was placed on the olecranon. Signals were
> digitised by a TI ADS1298 (gain 6, 24 bit, 2000 samples/s, clock
> 2.048 MHz ± 20 ppm) and resampled on the device to exactly 1000 Hz with
> a linear-phase equalising FIR filter (flat within ±0.06 dB over
> 20–450 Hz). Raw data were stored losslessly on a microSD card and
> streamed over Wi-Fi to the host, which issued time-stamped protocol
> markers. The device runs on battery only while worn; a hardware
> interlock disables the analog front end whenever USB power is present.

(Adjust the details to what you actually build and measure.)
