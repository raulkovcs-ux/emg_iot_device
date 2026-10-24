# 1. Research: what kind of device does FAMA-Net need?

This document derives the end-user device from the FAMA-Net thesis material
(`FAMA-Net Model Figures`, `FAMA-Net Thesis Review Sheet`) and from the
state of the art in wearable surface EMG (sEMG). The output is the design
direction that `02_specification.md` turns into numbered requirements.

## 1.1 What the model asks of the hardware

FAMA-Net (Figure 1 of the model figures) is an 8-channel forearm sEMG
gesture classifier with a fatigue head and two online adaptation tiers.
Reading the pipeline from left to right gives the hardware contract:

| Pipeline stage (thesis) | What it implies for the device |
|---|---|
| 8-channel sEMG armband, worn on the forearm, ≈1 kHz | 8 *bipolar* channels around the forearm, simultaneous sampling, ≥1 kHz |
| Butterworth 20–450 Hz, notch 50 Hz (+100/150 Hz), DC removal, per-channel z-score | Analog bandwidth must be flat from ≤20 Hz to ≥450 Hz; the AFE must tolerate electrode DC offsets that the digital DC removal later strips |
| STFT, 200 ms windows, 50 % overlap → [8 × F × T] | A new window every 100 ms; the full spectrum up to 500 Hz must be present (no 200 Hz armbands) |
| Shared multi-scale CNN, 96 k parameters, 0.4 MB | Fits in the SRAM/PSRAM of a modern MCU; float inference in ≈10 ms is feasible on a 240 MHz dual-core with SIMD |
| Tier A: causal AdaBN, EMA of BN statistics, α = 0.10 at session start, then 0.01 | BN layers must stay *unfolded* on the device so their running statistics can be updated at inference time |
| Tier B: SampEn < 80 % of baseline AND ŝ > 0.15 → 3–5 SGD steps on last 1–2 layers with pseudo-labels (softmax > 0.9), reset at session end | Needs on-device training of a small dense layer, a SampEn computation (m = 2, r = 0.2σ, N = 1000 samples) once per second, and a session concept with a hard weight reset |
| Latency limit ≤ 300 ms (review sheet §3) | Window hop (100 ms) + inference (≈10 ms) + Tier B update (≈40 ms) stays far below 300 ms on the device itself; a wireless round-trip to a PC is not needed |

The review sheet adds four hardware-relevant checklist items that the
current thesis cannot yet tick:

1. **"Exact sampling rate stated (the chapter gives a nominal 800–1000 Hz range)"** –
   the device must have a *known, crystal-referenced* sample rate.
2. **"MVC measurement procedure and anatomical landmarks for armband placement described"** –
   the device needs a repeatable placement reference (channel-1 marker, landmark procedure).
3. **"DB2 channel selection justified by matching the armband's spatial layout"** –
   the electrode geometry must be documented (8 equally spaced bipolar pairs, like the
   Delsys/Otto Bock electrodes used for NinaPro DB2's equally spaced ring).
4. **Leakage-free, reproducible recordings** – raw data must be logged losslessly with
   timestamps and protocol markers so that the temporal split (60/10/30 %) can be cut
   after the fact.

## 1.2 Why the off-the-shelf armbands are not enough

| Device | Channels / rate / resolution | Problem for FAMA-Net |
|---|---|---|
| Thalmic Myo (discontinued 2018) | 8 ch, 200 Hz, 8 bit | 200 Hz cuts the sEMG spectrum at 100 Hz; median-frequency fatigue analysis (H1) is impossible; no longer sold |
| OYMotion gForcePro+ | 8 ch, up to 1 kHz at **8 bit** or 500 Hz at 12 bit, BLE 4.x, dry electrodes [[1]](https://humanoid.guide/product/gforce-pro/) | 1 kHz only at 8-bit resolution; closed firmware, so no on-device Tier A/B |
| OpenBCI Cyton | 8 ch ADS1299, 250 Hz over the BLE dongle | Rate too low over wireless; gel/cup electrodes, not an armband |
| Delsys Trigno / Noraxon | Research grade, ≈2 kHz, 16 bit | Excellent signals but closed, expensive, separate sensors (no on-device model) |
| 3DC Armband (Université Laval) | 10 ch, 1000 sps, dry electrodes, 9-axis IMU, ≈150 USD, open Altium design [[2]](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6631507/) | Closest open precedent and shows a 3D-printed dry armband can beat the Myo; no fatigue/adaptation features, different MCU |

Conclusion: a purpose-built armband is justified. It is also a thesis
contribution in its own right: FAMA-Net becomes a *complete* wearable system
(sensor → on-device adaptation) instead of an offline model.

## 1.3 Key design decisions and the evidence behind them

### Analog front end: TI ADS1298 (8 × 24-bit ΔΣ, simultaneous)
* Eight simultaneously sampled channels with PGA, internal reference,
  right-leg drive (RLD) and data rates from 250 SPS to 32 kSPS
  (1 kSPS available in high-resolution mode). Input-referred noise is
  specified as 4 µVpp in a 150 Hz bandwidth at gain 6 [[3]](https://www.ti.com/product/ADS1298).
  That is well below sEMG amplitudes (≈50 µV–5 mV).
* It is the standard choice for wearable biopotential designs. The
  alternative ADS1299 targets EEG (lower noise, higher price, slower); EMG
  does not need EEG noise levels.
* Unipolar 3.0 V analog supply with VREF = 2.4 V and gain 6 gives a
  ±400 mV range. Dry electrodes can show DC offsets of a few hundred mV,
  and this range absorbs them without saturating (1 LSB = 47.7 nV).
* **External 2.048 MHz ±20 ppm oscillator** instead of the internal one
  (internal tolerance is in the percent range). This makes the sample
  rate *exactly* 1000.00 Hz ± 0.02 Hz and answers the review-sheet item
  directly.
* Verification note: the KiCad 7.0 stock symbol for the ADS1298 swaps the
  names of pins 3 and 4. The TI pin table (pin 3 = IN7N, pin 4 = IN7P,
  confirmed against two independent pinout sources) is used in
  `hardware/lib/famaband.kicad_sym`. Without this fix, channel 7 would be
  recorded with inverted polarity.

### Electrodes: active dry electrodes ("pods")
* Gel electrodes are best for signal quality but impractical for an armband
  that is put on repeatedly. Dry electrodes have high, variable skin
  impedance, so each electrode is **buffered right at the skin** by a
  CMOS op-amp (OPA2376: 0.2 pA bias, 7.5 nV/√Hz). This is the architecture of
  the published low-cost active dry sensors [[4]](https://arxiv.org/pdf/2009.02575):
  buffering both electrodes and driving their shields reduces
  displacement-current interference.
* Each pod has two 10 mm ENIG (gold) pads on the PCB bottom, 20 mm apart.
  Those values follow the SENIAM recommendations (electrode diameter ≤ 10 mm,
  inter-electrode distance ≈ 20 mm, bipolar pair parallel to the muscle fibres).
  PCB pads as electrodes are used in the PUCP multichannel dry array
  [[5]](https://repositorio.pucp.edu.pe/items/067b3ef8-5854-4ccf-81ea-3031974476d6).
* A solder-masked **guard ring** around each electrode is driven by that
  electrode's own buffer output (bootstrapped guard). This cuts leakage and
  capacitive pickup at the highest-impedance node.
* Eight identical pods sit equally spaced around the forearm, which matches
  the equally spaced electrode ring used in NinaPro DB2 (8 of 12 channels).
* A separate **bias (DRL) electrode** on a bony landmark (gel snap
  electrode at the olecranon or the ulnar styloid) closes the right-leg-drive
  loop. That raises the effective common-mode rejection and keeps the
  body inside the buffers' common-mode range (~1.5 V).

### MCU: ESP32-S3 (WROOM-1-N16R8 module)
* 240 MHz dual-core Xtensa LX7 with vector (SIMD) instructions and 8 MB
  PSRAM. FAMA-Net (96 k parameters, a few MFLOPs per window) runs in float32
  within the 100 ms window hop. Tier B (3–5 SGD steps on the last dense
  layer) and SampEn (O(N²) with N = 1000, once per second) fit easily.
* BLE 5 and Wi-Fi from a single pre-certified module. This avoids an RF
  design and an RF certification for the prototype.
* Native USB for firmware updates and SD-card offload. The unit is only
  used **off the arm**; see the safety interlock below.

### Storage, sensing, power
* **microSD (4-bit SDMMC)**: a lossless 24-bit raw log of every session is
  the research ground truth. Wireless links can drop packets, and the SD
  log cannot.
* **6-axis IMU (LSM6DSL)**: forearm pronation/supination are the
  most-confused gestures in the pseudo results (15 % and 14 %). The IMU
  provides orientation context and a motion-artefact flag.
* **1S Li-Po 600 mAh**, buck-boost to 3.3 V (TPS63001, forced PWM, so no
  low-frequency PFM ripple in the EMG band), ultra-low-noise 6.5 µVrms LDO
  (LP5907) for the analog 3.0 V rail, fuel gauge (LC709203F). The power
  budget (`03_hardware_design.md`) gives ≈5.4 h with BLE streaming and
  ≈3.6 h with Wi-Fi (estimates to be measured), against the 2 h sessions in the protocol.

### Safety architecture (worn on a person)
* **Battery-only while worn.** A hardware interlock (2N7002 on VBUS) holds
  the analog LDO in shutdown whenever USB power is present, regardless of
  firmware. The electrodes and pods are therefore unpowered while a
  mains-referenced PC or charger is attached.
* **Patient-current limiting**: a 100 kΩ resistor in series with every
  electrode and with the bias output. Even with a node shorted to a supply
  rail (single fault), DC current into the body stays below 33 µA. The
  normal-condition current is set by the op-amp bias current (pA). These
  numbers are compared with the IEC 60601-1 type-BF patient leakage limits
  (10 µA normal condition, 50 µA single fault, DC) in `06_safety_and_ethics.md`.
* A hard power slide switch disconnects the battery.

## 1.4 Alternatives considered and rejected

| Option | Why rejected |
|---|---|
| Stream raw data to a PC and run FAMA-Net there | Simpler firmware, but the thesis claims deployability (9 ms latency, 0.4 MB model on the "target device"); a PC-tethered system cannot back that claim. Raw streaming is kept as a *second* mode for data collection |
| Passive dry electrodes (no buffer) | Skin impedance mismatch converts mains common-mode into differential noise; strongly motion sensitive |
| Intan RHD2216 (digital EMG amplifier) | Excellent, but 16-bit, higher bandwidth than needed, smaller ecosystem for hobby assembly |
| nRF5340 (BLE MCU) | Lower power, but too little RAM and compute for float inference plus SampEn plus on-device fine-tuning without heavy quantisation work |
| Myo-style rigid segment ring with flex interconnect | Better looking, but much harder to build and repair for a thesis; a star topology with one shielded cable per pod is serviceable |

## 1.5 Sources

1. humanoid.guide, gForcePro+ product listing (retailer specs, secondhand): https://humanoid.guide/product/gforce-pro/
2. Côté-Allard et al., "A Low-Cost, Wireless, 3-D-Printed Custom Armband for sEMG Hand Gesture Recognition", *Sensors* 2019: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6631507/
3. Texas Instruments ADS1298 product page and datasheet (SBAS459): https://www.ti.com/product/ADS1298 , https://www.ti.com/lit/ds/symlink/ads1298.pdf
4. "Low-cost Active Dry-Contact Surface EMG Sensor for Bionic Arms", arXiv:2009.02575: https://arxiv.org/pdf/2009.02575
5. "Surface EMG multichannel array using active dry sensors for forearm signal extraction", PUCP: https://repositorio.pucp.edu.pe/items/067b3ef8-5854-4ccf-81ea-3031974476d6
6. OpenElectronicsLab ADS1298 breakout (independent pinout cross-check): https://github.com/OpenElectronicsLab/ads1298-breakout
7. TI E2E forum, ADS1298 noise and VCAP discussions: https://e2e.ti.com/support/data-converters-group/data-converters/f/data-converters-forum/114658/ads1298-noise
8. SENIAM recommendations for sensor location and electrode configuration (Hermens et al., 2000), www.seniam.org
