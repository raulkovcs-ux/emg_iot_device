# 7. Ordering, assembly and bring-up

## 7.1 Ordering

| Item | Source | Notes |
|---|---|---|
| Hub PCB + assembly | Any PCBA service (e.g. JLCPCB, PCBWay, Aisler) | Upload `hardware/fab/hub/hub_gerbers.zip`, `hub_bom.csv`, `hub_cpl.csv`. 4 layers, 1.6 mm, 1 oz, HASL-LF or ENIG. Double-sided assembly: four pod connectors and the battery connector are on the bottom. Have the ADS1298, ESP32 module and LGA IMU placed by the service |
| Pod PCBs (≥ 10) | Any PCB fab | `hardware/fab/pod/pod_gerbers.zip`, 2 layers, 1.6 mm, **ENIG required** (hard gold better), assemble by hand (0603 / VSSOP-8) |
| Oscillator | SiT8008, programmed for 2.048 MHz, 3.3 V, ± 20 ppm, 2.5 × 2.0 mm | Order the programmed part, or use any 2520 CMOS oscillator at exactly 2.048 MHz |
| Battery | 1S Li-Po ≈ 600 mAh, ≤ 40 × 30 × 6.5 mm, **with protection PCM**, JST PH 2.0 lead | **Check the lead polarity against J2 (pin 1 = +)**: vendors differ |
| Cables | 4-core shielded, OD ≤ 2.5 mm; JST GHR-04V-S housings + SSHL-002T-P0.2 crimps | 8 cables, 25–35 cm |
| Bias lead | JST GHR-02V-S to a 4 mm snap + disposable Ag/AgCl electrodes | |
| Strap | 25 mm elastic strap + hook-and-loop, 2 lengths | |
| Screws | 3 × M2 × 12 countersunk | |
| Enclosures | 3D print `mechanical/stl/*.stl` | PETG or biocompatible resin |

## 7.2 Assembly order

1. Visually inspect the assembled hub. Check the ADS1298 for bridges on
   the 0.5 mm pins.
2. **Before the battery goes in:** apply 3.7 V from a current-limited
   lab supply (100 mA) at J2 with the switch ON. Expected: 3.3 V on the
   test pads and less than 60 mA with no firmware.
3. Flash the firmware over USB-C (`05_firmware.md` §5.5). The USB port
   powers the board for flashing.
4. Assemble the pods (§4.3). Test each pod alone (§7.3, step 5) before
   it goes into a housing.
5. Fit the battery and close the hub (3 × M2).

## 7.3 Bring-up tests (record the results in a log; they become thesis evidence)

| Step | Test | Pass criterion |
|---|---|---|
| 1 | Boot log over USB (`idf.py monitor`) | "ADS1298 ready (ID 0x92)", "FAMA-Net loaded" (with model), Wi-Fi IP printed |
| 2 | **Interlock**: USB plugged in, try to start a session | Session refused; 3.0 V rail (C near U4) < 0.1 V |
| 3 | menuconfig → FAMA-Band → ADS1298 input = "Inputs shorted"; USB unplugged, start a session | Noise per channel ≤ 1.5 µVrms (20–450 Hz) at the ADS input |
| 4 | ADS1298 input = "Internal test signal" (square wave) | Same amplitude on all 8 channels (±1 %), correct polarity, no missing samples |
| 5 | Pod on a resistor phantom (two 100 kΩ resistors to a common node driven by a 100 Hz, 1 mVpp generator, bias pin to the same node) | 1 mVpp ± 2 % per channel, CMRR test: same signal on both pod inputs gives < 10 µVpp |
| 6 | **Sample rate**: record 10 min, count frames against a reference clock | 1000.00 Hz ± 0.02 Hz (600 000 ± 12 frames in 600 s) |
| 7 | Link loss: stream 30 min over Wi-Fi in the lab | `lost` = 0 or < 0.01 %; the SD log is gap-free in every case |
| 8 | Battery: full charge → continuous session with streaming until shutdown | ≥ 2.5 h (requirement PWR-1); record the actual runtime |
| 9 | On-device inference timing (log `esp_timer` around `fama_net_forward`) | < 100 ms per window (report the measured value in the thesis) |

## 7.4 Safety verification (before the first person)

| Test | Method | Pass |
|---|---|---|
| Fault current | Short each electrode node and the bias output to +3V0 and to GND in turn; measure the current through a 10 kΩ load to the opposite rail | ≤ 35 µA in every case |
| Interlock timing | Scope on the 3.0 V rail while plugging in USB | Rail < 0.3 V within 10 ms |
| Surface temperature | Thermocouple on the pod and hub bottoms after 2 h streaming | Pod < 1 °C above ambient skin model; hub < 41 °C |
| Insulation | Inspect housings: no exposed copper except the electrode pads | Pass |

## 7.5 First on-body check (researcher on themself, after §7.4 has passed)

1. Put the band on (`08_wearing_and_session_protocol.md`).
2. Run `python -m famaband.contact`. All channels should read "ok":
   |DC| < 250 mV and 50 Hz < 50 µV.
3. Relax, then make a fist. EMG RMS should rise from a few µV to tens or
   hundreds of µV on the flexor channels.
4. Record 2 minutes with `python -m famaband.record` and inspect the
   spectrum (median frequency typically 60–120 Hz).
