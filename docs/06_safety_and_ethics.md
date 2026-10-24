# 6. Safety and ethics for use on people

FAMA-Band is a **research prototype, not a medical device**. It has not
been tested against IEC 60601-1 (electrical safety) or IEC 60601-1-2
(EMC), and it must not be used for diagnosis or treatment. The design
applies the principles of those standards where they matter for a body-worn
sEMG recorder. This document is the basis for the risk section of an
ethics application. **Do not record from any person before the
institution's ethics approval (or a documented exemption) and signed
informed consent are in place.** The review sheet already lists
"Consent form signed; approval number or exemption statement" as a
required dataset item.

## 6.1 Hazard analysis (ISO 14971 style, abbreviated)

Severity (S) and probability (P) are rated 1 (negligible / improbable)
to 4 (serious / frequent), each before → after control.

| # | Hazard | Cause | Control in this design | S | P | Residual |
|---|---|---|---|---|---|---|
| H1 | Electric shock / leakage current from mains | Band worn while connected to a mains-powered PC or charger through USB | Battery-only operation. **Hardware interlock**: VBUS present forces the analog LDO off, unpowering pods, bias drive and ADS analog section, independent of firmware. Firmware refuses sessions while VBUS is present (second layer). "NOT WHILE CHARGING" on the lid and "DO NOT WEAR WHILE USB CONNECTED" on the PCB. Procedure step in `08_wearing_and_session_protocol.md` | 4→4 | 2→1 | Acceptable |
| H2 | DC current through tissue (electrochemical irritation) | Component failure shorts an electrode node to a supply rail | 100 kΩ series resistor on every skin contact: ≤ 33 µA at 3.3 V (the type-BF single-fault limit is 50 µA DC). Normal-condition current is the op-amp bias current (pA) | 2→2 | 2→1 | Acceptable |
| H3 | Battery fire or overheating | Short circuit, over-charge, crushing | Cell with an integrated protection PCM (mandatory BOM item); MCP73831 charge termination at 4.20 V; PTC on VBUS; hard power switch; cell in a closed pocket under the PCB, not in skin contact; charging only off the arm | 4→4 | 2→1 | Acceptable |
| H4 | Skin irritation or allergy | Nickel under ENIG gold; plastics; sweat over 2 h | Screening question for nickel allergy (exclude, or use hard-gold or stainless electrodes). PETG or biocompatible resin housings. Electrodes cleaned with 70 % isopropanol before every subject. Skin inspected after removal. Stop on discomfort | 2→2 | 2→1 | Acceptable |
| H5 | Pressure marks, restricted circulation | Strap too tight for 2 h | Elastic strap; "two fingers fit under the strap" rule; check at every block transition; subject can stop at any time | 2→1 | 2→1 | Acceptable |
| H6 | Overexertion or muscle injury | Fatigue protocol at 50–75 % MVC, repeated MVCs | Healthy volunteers only; PAR-Q style screening; warm-up; rest periods as specified; subject-controlled stop; no MVCs with a recent forearm injury | 3→3 | 2→1 | Acceptable with supervision |
| H7 | Burn from warm electronics | Hub dissipates ≈0.5 W | Hub sits on the outside of the strap, no skin contact; pods dissipate < 3 mW | 2→1 | 1→1 | Acceptable |
| H8 | Infection transfer between subjects | Shared band | Disinfect electrodes and strap contact areas between subjects; the strap is washable or per-subject | 2→1 | 2→1 | Acceptable |
| H9 | RF exposure | 2.4 GHz radio near the body | Pre-certified ESP32-S3 module, ≤ 20 dBm conducted, low duty cycle; comparable to a smartwatch | 1 | 1 | Acceptable |
| H10 | Data protection breach | Identifiable recordings | Pseudonymous IDs only (S01–S10) on the device and in files; key list stored separately; local Wi-Fi network, no cloud | 2→1 | 2→1 | Acceptable |

## 6.2 Electrical-safety numbers (from `03_hardware_design.md` §3.5)

| Quantity | Design value | Type-BF limit (IEC 60601-1, DC) |
|---|---|---|
| Patient auxiliary current, normal condition | < 1 µA (pA bias + sub-µA RLD correction) | 10 µA |
| Patient auxiliary current, single fault (any skin node at a rail) | ≤ 33 µA | 50 µA |
| Connection to mains while worn | Prevented by battery operation + VBUS interlock | — |

**Bring-up must confirm these on real hardware** (`07_assembly_and_bringup.md` §7.4):
* Measure the current into a 10 kΩ dummy load from each electrode
  and the bias pin, with the node shorted to each rail.
* Confirm the 3.0 V rail drops to 0 V within milliseconds of plugging in USB.

## 6.3 Operating rules (also printed in the session checklist)

1. Charge, flash and offload data **only with the band off the arm**.
2. Only healthy adult volunteers who have passed the screening
   questions; no implanted electronic devices (pacemaker, ICD, neurostimulator).
3. A researcher is present for the whole session. The subject may stop
   at any time without giving a reason.
4. Inspect the band before each session: no cracked housing, no damaged
   cable, no electrode corrosion, battery not swollen.
5. Clean the electrodes before and after each subject. Inspect the skin
   afterwards and note any redness in the session log.
6. Do not use near MRI or in wet environments.

## 6.4 Ethics application checklist

- [ ] Ethics committee approval number (or exemption statement) obtained before the first recording
- [ ] Participant information sheet: purpose, procedure (2 sessions × about 2 h, MVC and fatiguing contractions), risks from §6.1, data handling, right to withdraw
- [ ] Informed consent form signed and stored separately from the data
- [ ] Screening questionnaire: age 18–40, no neuromuscular or forearm disorders, no recent injury, no nickel allergy, no implanted electronics
- [ ] Data management plan: pseudonymisation, storage location, retention period, and whether the dataset will be published
- [ ] Incident procedure: what to do on skin reaction, pain or device malfunction
- [ ] Device description for the committee: this document plus `02_specification.md`
