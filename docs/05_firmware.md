# 5. Firmware and host software

## 5.1 Layout

```
firmware/                       ESP-IDF v5.3 project (ESP32-S3)
  main/app_main.c               tasks, sessions, commands
  main/ads1298.c                AFE driver (2000 SPS, gain 6, RLD, external clock)
  main/power.c                  USB interlock, analog-rail enable, LC709203F gauge, LEDs
  main/storage.c                microSD .fbr logger
  main/link.c                   Wi-Fi UDP (default) or BLE GATT (CONFIG_FAMA_LINK_BLE)
  main/imu.c                    LSM6DSL
  components/fama_dsp/          portable C core, also built for the host and tested
    src/dsp.c                   decimator, filters, z-score, STFT, SampEn
    src/fama_net.c              FAMA-Net graph runtime with unfolded BN (Tier A)
    src/tier_b.c                SampEn-triggered head fine-tuning (Tier B)
    src/pipeline.c              per-sample pipeline, session logic
    src/proto.c                 packet framing (CRC-16)
    include/fama_coeffs.h       generated from host/famaband/dsp_ref.py
host/famaband/                  Python package (lab side)
  dsp_ref.py, net_ref.py, pipeline_ref.py   reference implementations (the spec of the C code)
  model.py, export.py           PyTorch FAMA-Net and export to the device blob
  protocol.py, io.py            packet format, .fbr files, converters
  link.py, record.py, contact.py, protocol_runner.py   tools for running sessions
  simulator.py, simulator_device.py           synthetic fatiguing sEMG, software device
host/tests/                     pytest: C vs reference, spec checks, end-to-end over UDP
```

## 5.2 Real-time structure

| Task | Core | Prio | Job |
|---|---|---|---|
| `acq` | 1 | 20 | Woken by DRDY (2 kHz). Reads 27 bytes over SPI (≈30 µs) and pushes the frame to three lock-free rings: raw (SD), DSP, and stream after an inline decimate-by-2 |
| `dsp` | 1 | 10 | `fama_pipeline_push`: decimator → filters → z-score; every 100 ms STFT → FAMA-Net (Tier A) → softmax → Tier B; SampEn every 1 s |
| `sd` | 0 | 5 | Packs 50 raw frames (25 ms) per packet and writes events; `fsync` every 2 s |
| `link` | 0 | 6 | Sends 1 kHz stream packets (25 frames over UDP, 9 over BLE), results and status |
| `imu` | 0 | 4 | 104 Hz, 10 samples per packet, stamped with the 1 kHz frame index |
| `ui` | 0 | 2 | 1 Hz status; button: long press starts/stops a session, short press adds a marker |

No consumer can stall acquisition. Each ring holds 2 s, and overflows
are counted and reported in the status packet (`dropped_frames`).

## 5.3 On-device FAMA-Net

* **Graph runtime** (`fama_net.c`). The ops are CONV2D (grouped, so it
  covers depthwise), BN, ReLU, CONCAT, GAP and DENSE, in float32 with
  weights in PSRAM. BatchNorm is **not folded** into the convolutions,
  because Tier A has to update its statistics.
* **Reference topology** (`net_ref.build_fama_net`, `model.FamaNet`),
  following model figure 1:
  1. input [8 × 33 × 9]
  2. multi-scale block: 3×3, 5×5 and 7×7 convolutions (16 channels each), each followed by BN + ReLU, then concatenated
  3. two depthwise-separable blocks (48 → 96 → 128 channels, stride (2, 1))
  4. GAP, then a shared linear layer (128)
  5. gesture head 128 → 64 → 8, fatigue head 128 → 32 → 1

  That is 59.5 k parameters, a 243 kB blob and about 0.9 MB of PSRAM in
  use. The widths are arguments: the thesis configuration (about 96 k
  parameters) exports the same way, provided the layer structure matches.
* **Tier A** (causal AdaBN). For every window, each BN layer takes the
  per-channel mean and variance of its input over the window's
  time-frequency positions, then updates its running statistics with
  α = 0.10 for the first 300 windows (30 s) and α = 0.01 afterwards.
  `fama_pipeline_session_start` resets the statistics to the pre-trained
  values.
* **Tier B.** Every window is recorded: the input to the first trainable
  head layer, the softmax and ŝ, in a ring of the last 24 windows (the
  figure's "last 20–30"). Weights change only if
  1. SampEn < 0.8 × baseline (baseline = median SampEn of the first 60 s
     of the session), and
  2. mean ŝ over those 24 windows > 0.15.

  It then keeps the windows with softmax > 0.9 (at least 8), takes their
  argmax as pseudo-labels, and runs 4 SGD steps (lr 1e-3, configurable
  3–5) on the last **two** gesture-head dense layers, with the backbone
  and fatigue head frozen. A refractory period (default 30 s) stops
  back-to-back updates. Report it alongside "triggers per session" in
  the thesis. `fama_pipeline_session_end` restores the pre-trained
  weights.
* **Latency.** The window hop is 100 ms. Inference cost is about
  5 M multiply-accumulates per window. In plain float32 C on the
  240 MHz LX7 that is an estimated 20–80 ms, within the hop and far
  inside the 300 ms limit. The thesis's "9 ms" figure would need ESP-NN
  int8 kernels for the convolutions; BN can stay a float affine step, so
  Tier A still works. **Measure it on the board** (`esp_timer` around
  `fama_net_forward`) before quoting a number.

## 5.4 Packet and file format

One framing for the radio link and the SD log (`fama_proto.h`, `protocol.py`):

| Offset | Size | Field |
|---|---|---|
| 0 | 2 | magic `0xFA4E` |
| 2 | 1 | version (1) |
| 3 | 1 | type: 0x01 EMG 1 kHz, 0x02 IMU, 0x03 result, 0x04 status, 0x05 marker, 0x06 session, 0x11 raw EMG 2 kHz, 0x80 command |
| 4 | 4 | seq (gaps = lost packets) |
| 8 | 4 | frame: index of the first sample (2 kHz timebase for raw, 1 kHz otherwise) |
| 12 | 2 | n items |
| 14 | 2 | payload length |
| 16 | len | payload (EMG: n × 8 × int24 LE, LSB = 47.68 nV) |
| 16 + len | 2 | CRC-16/CCITT-FALSE |

**`.fbr` session file.** It starts with `FAMABAND`, a u16 version, a
u16 JSON length and JSON metadata (device, firmware, session, gain,
VREF, LSB, clock, start frame), padded to 512 bytes. Packets follow.
The device writes raw 2 kSPS EMG, IMU, markers, FAMA-Net results, status
and session events. The host recorder writes the same format from the
radio stream. `python -m famaband.io file.fbr --npz out.npz` (or
`--csv`) converts it, and `famaband.io.read_fbr()` loads it with lost
frames counted.

**Commands (host → device, type 0x80):**

* `SESSION_START <id>`
* `SESSION_END`
* `MARKER <u16 code> <label>`: stamped by the device with its current
  frame index, so it shares the EMG timebase
* `SET_MODE` (stream / inference / both)
* `PING`

## 5.5 Building and flashing

```bash
# ESP-IDF v5.3.x installed and exported (. $IDF_PATH/export.sh)
cd firmware
idf.py set-target esp32s3
idf.py menuconfig            # FAMA-Band -> Wi-Fi SSID/password, UDP port
idf.py build flash monitor   # board OFF the arm, USB connected
# BLE instead of Wi-Fi:
idf.py -B build_ble -D SDKCONFIG=build_ble/sdkconfig -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.ble" build
```
Both variants are verified to compile with ESP-IDF v5.3.1. Wi-Fi image:
905 kB; BLE image: 686 kB; static internal RAM 40 % used.
The model goes onto the card: `python -m famaband.export --checkpoint model.pt --out /media/SD/fama_net.bin`.
Without a model the device still streams and logs, and computes SampEn.

## 5.6 Tests (`cd host && python -m pytest`)

| Test file | What it proves |
|---|---|
| `test_dsp.py` | Decimator + sinc³ flat ±0.5 dB over 20–450 Hz and ≥ 50 dB alias rejection; why 1 kSPS fails; band-pass and notch specs; C decimator, filters, z-score, STFT and SampEn equal the reference; SampEn equals a brute-force implementation and drops under simulated fatigue |
| `test_net.py` | C graph runtime = NumPy forward; Tier A AdaBN identical in C and NumPy, and reset restores exactly; corrupt blobs rejected; PyTorch model → device graph gives identical outputs |
| `test_pipeline.py` | Whole C pipeline = Python reference on 100 s of synthetic fatiguing sEMG; window count and rate; SampEn baseline; Tier B fires on exactly the same windows in both, never before fatigue, and respects the refractory period; session end restores weights; **causality**: changing future samples never changes past outputs (leakage checklist) |
| `test_protocol.py` | C packer ↔ Python parser, CRC check value, resync after corruption, `.fbr` round trip |
| `test_host_tools.py` | Recording and the full protocol runner over real UDP against the software device; markers monotonic in the device timebase; contact check flags offset and mains problems |

Current status: **30 tests, all passing.** The C core also compiles
warning-free with `-Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wdouble-promotion`.
