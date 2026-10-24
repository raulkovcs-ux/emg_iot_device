# 2. Device specification – FAMA-Band rev A

**FAMA-Band** is an 8-channel active-dry-electrode sEMG armband that records,
logs and streams forearm EMG at exactly 1 kHz and runs FAMA-Net (both
adaptation tiers) on the device. This document is the requirement baseline.
Every requirement has an ID, a source (thesis item or engineering need) and
a verification method. Status is the design status in this repository. All
pass/fail checks on real hardware are still to be done during bring-up
(`07_assembly_and_bringup.md`).

Legend for verification: **A** analysis/calculation, **D** design
inspection, **T** test on hardware, **S** software test in this repo (runs in CI / locally).

## 2.1 Signal acquisition

| ID | Requirement | Source | Design value | Verif. |
|---|---|---|---|---|
| ACQ-1 | 8 bipolar sEMG channels, simultaneously sampled | Model fig. 1 | ADS1298, 8 differential channels, one ΔΣ modulator per channel | D |
| ACQ-2 | Sample rate exactly 1000 Hz, stated with tolerance | Review §4 "exact sampling rate" | Output 1000.00 Hz ± 20 ppm: 2.048 MHz oscillator, ADS1298 at 2000 SPS (HR mode, DR = 100), FIR + decimate-by-2 in firmware | A, T |
| ACQ-3 | Usable band covers 20–450 Hz with < 0.5 dB droop | Model fig. 1 (filter) | Sinc³ at 2 kSPS (−3 dB at 524 Hz) + firmware equaliser FIR → ±0.5 dB over 20–450 Hz¹; EMI RC ≥ 3.4 kHz DM | A, S |
| ACQ-4 | Resolution ≥ 16 effective bits over the EMG band | Engineering | 24-bit, 47.7 nV LSB; 105 dB dynamic range (full-scale sine / 1.61 µVrms) ≈ 17.1 ENOB | A, T |
| ACQ-5 | Input range tolerates ±300 mV electrode DC offset | Dry electrodes | ±400 mV (VREF 2.4 V, gain 6) | A |
| ACQ-6 | Input-referred noise (20–450 Hz) < 5 µVrms incl. electrode buffers | Engineering | 1.61 µVrms calculated (budget in `03_hardware_design.md` §3.3), excluding electrode–skin noise | A, T |
| ACQ-7 | Common-mode rejection supported by driven right leg | Engineering | RLD from all 16 inputs, 1 MΩ ‖ 1.5 nF loop, bias electrode on bony landmark | D, T |
| ACQ-8 | Electrode geometry: Ø ≤ 10 mm, IED 20 mm, aligned with forearm axis | SENIAM, review §4 | Ø 10 mm ENIG pads, 20.0 mm IED, pods equally spaced around the forearm | D |
| ACQ-9 | Spatial layout comparable to NinaPro DB2 equally spaced ring | Review §4 DB2 channel selection | 8 pods at 45° spacing, channel 1 over extensor carpi radialis (marked) | D |
| ACQ-10 | IMU for orientation / motion artefact flag | Pseudo results: pronation/supination confusions | LSM6DSL 6-axis, 104 Hz, timestamped with EMG frame counter | D, S |

¹ *Note on ACQ-3:* the ADS1298 digital decimation filter is a sinc³ whose
−3 dB point is at 0.262 × f_DR. At f_DR = 1 kSPS that is ≈262 Hz,
**below 450 Hz**. The device therefore samples internally at **2 kSPS**
(DR = 100, −3 dB ≈ 524 Hz, droop at 450 Hz ≈ −2.2 dB). The firmware applies
a 63-tap linear-phase least-squares FIR (equaliser + anti-alias; with the
sinc³ the passband 20–450 Hz is flat within ±0.06 dB and everything ≥ 560 Hz is ≥ 58 dB down) and decimates by 2 to the 1000.00 Hz output stream. The
output rate stays exactly 1000 Hz, and the 20–450 Hz band is preserved as
the thesis filter assumes. This is verified in software (`host/tests/test_dsp.py::test_decimator_response`).

## 2.2 Processing (on device)

| ID | Requirement | Source | Design value | Verif. |
|---|---|---|---|---|
| PRC-1 | Pre-processing identical to the thesis: 4th-order Butterworth 20–450 Hz, notches 50/100/150 Hz, DC removal, per-channel z-score | Model fig. 1, review §4 | Biquad cascade (SOS), notch Q = 30, causal running z-score (EMA, τ = 10 s) | S |
| PRC-2 | 200 ms windows, 50 % overlap → [8 × F × T] STFT | Model fig. 1 | 200 samples, hop 100; STFT n_fft 64, hop 16, Hann → [8 × 33 × 9] | S |
| PRC-3 | SampEn with m = 2, r = 0.2σ, N = 1000 per channel | Review §2 | Exact O(N²) SampEn, 1 Hz update, channel-mean; baseline = median of first 60 s | S |
| PRC-4 | FAMA-Net inference latency ≤ 300 ms end to end | Review §3 | Hop 100 ms + inference ≈ 10 ms (target, T) | A, T |
| PRC-5 | Tier A: causal AdaBN, α = 0.10 during warm-up (first 30 s), then 0.01 | Model fig. 1, review §5 | Implemented on unfolded BN layers (`fama_net.c`) | S |
| PRC-6 | Tier B: trigger when SampEn < 0.8·baseline **and** ŝ > 0.15; pseudo-labels with softmax > 0.9; 3–5 SGD steps on last layer; reset at session end | Model figs. 1–2 | `tier_b.c`, buffer of 24 windows, 4 steps, lr 1e-3, reset on `SESSION_END` | S |
| PRC-7 | Tier A/B use only past windows (causality, leakage checklist) | Review §1 leakage checklist | Ring buffers only; no look-ahead; verified by a causality unit test | S |
| PRC-8 | Model weights replaceable from the thesis training code | Reproducibility | `host/famaband/export.py` writes a versioned blob; device loads it from SD / USB | S |

## 2.3 Data, connectivity, timing

| ID | Requirement | Source | Design value | Verif. |
|---|---|---|---|---|
| DAT-1 | Lossless raw log of every sample, 24-bit, with frame counter | Review §1, §6 | microSD, `.fbr` binary (documented in `05_firmware.md`), 1 file per session | S, T |
| DAT-2 | Live stream for monitoring and the host protocol | Protocol | BLE 5 (2M PHY) or Wi-Fi UDP, 25 frames (25 ms) per packet, CRC-16 | S, T |
| DAT-3 | Protocol markers (gesture cue, MVC, G/F block) stored in the same timebase | Review §1 temporal split | Host → device marker command; markers written into the SD log at the current frame index | S |
| DAT-4 | Session concept with explicit start/end (Tier B reset) | Model fig. 2 | `SESSION_START` / `SESSION_END` commands and button long-press | S |
| DAT-5 | Open converters to NumPy / CSV / HDF5 | Reproducibility | `host/famaband/io.py` | S |

## 2.4 Power, mechanics, environment

| ID | Requirement | Source | Design value | Verif. |
|---|---|---|---|---|
| PWR-1 | ≥ 2.5 h continuous recording + streaming (2 h session + setup) | Protocol (2 × 2 h sessions) | 600 mAh Li-Po: ≈5.4 h BLE, ≈3.6 h Wi-Fi (estimate, `03_hardware_design.md` §3.6) | A, T |
| PWR-2 | Charge via USB-C, < 2 h | Usability | MCP73831 at 455 mA | A |
| PWR-3 | Battery state visible to host | Usability | LC709203F fuel gauge, % in status packets | S |
| MEC-1 | Fits forearm circumference 20–32 cm | Subjects 21–28 y, both sexes | Elastic 25 mm strap, pods slide on strap; 2 strap lengths | D |
| MEC-2 | Total mass < 120 g | Comfort for 2 h | Hub ≈ 38 g incl. battery, pod ≈ 5 g × 8, strap/cables ≈ 25 g → ≈ 103 g (estimate) | A |
| MEC-3 | Repeatable placement | Review §4 "anatomical landmarks" | Channel-1 pod marked; placement at 1/3 of the elbow–wrist distance, ch1 over ECR (procedure in `08_wearing_and_session_protocol.md`) | D |
| MEC-4 | Skin-contact materials suitable for 2 h contact | Safety | ENIG/hard-gold electrodes; housings PETG or medical-grade resin; nickel-allergy screening question | D |
| ENV-1 | Indoor lab use, 15–35 °C, sweat exposure on electrodes | Protocol | Electrodes wiped with 70 % isopropanol between subjects | D |

## 2.5 Safety (detail in `06_safety_and_ethics.md`)

| ID | Requirement | Design value | Verif. |
|---|---|---|---|
| SAF-1 | No connection to mains-referenced equipment while worn | Battery only; hardware VBUS interlock powers down the analog rail and pods; label + procedure | D, T |
| SAF-2 | Patient DC current < 10 µA normal, < 50 µA single fault (IEC 60601-1 type BF targets) | 100 kΩ series on every skin contact: ≤ 33 µA with any node at a rail; normal ≈ pA | A, T |
| SAF-3 | Battery protected against over-charge/over-discharge/short | Cell with integrated PCM protection (required in BOM), PTC on VBUS, hard switch | D |
| SAF-4 | Surface temperature of skin-contact parts < 41 °C | Pods dissipate < 3 mW; hub < 0.6 W and not in skin contact | A, T |
| SAF-5 | Research use only, not a medical device | Labelling, ethics approval before human use | D |

## 2.6 Out of scope for rev A
* Medical certification (IEC 60601-1, IEC 60601-1-2 EMC). The design
  follows its principles for patient-current limits and isolation by
  battery operation, but is a research prototype.
* Force/MVC sensing on the band. MVC and target load use an external hand
  dynamometer or load cell read by the host software (`host/famaband/protocol_runner.py`).
* Over-the-air firmware update (USB only while off the arm).
