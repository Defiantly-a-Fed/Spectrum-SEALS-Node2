# RF Ragnar Node 2 v1 — Working Milestone

This directory contains the **calibrated passive RF detector** for Spectrum SEALS Node 2 and supporting evidence.  The detector uses a Nooelec NESDR SMArt v5 (RTL‑SDR) to capture raw I/Q samples and perform FFT analysis to detect abnormal RF activity across multiple ISM bands.

## Files included

* `ragnar_rf_node2_v1.py` — Python script implementing the calibrated detector (v1).
* `README.md` (this file) — Overview of the Node 2 detector and milestone.

The script contains two phases: calibration (learning the ambient noise floor) and live scanning (tracking and locking onto peaks with confidence scoring).  It logs events to JSONL and generates an operator HTML dashboard.  To run it, install `numpy` (e.g. `pip install numpy`), ensure `rtl_sdr.exe` and its DLLs are in the project `bin` folder, then run:

```
python ragnar_rf_node2_v1.py --cycles 12 --calibration-cycles 3 --capture-seconds 0.45
```

After calibration, cause a legal local signal (car key fob, 433 MHz weather sensor, etc.) to see the detector track and lock onto peaks.  The current HTML panel auto‑refreshes every two seconds.

## Tested bands

* 315 MHz ISM
* 433.92 MHz ISM
* 868 MHz ISM
* 915 MHz ISM
* 1090 MHz ADS‑B

## Current limitations

The NESDR lane covers up to ~1.7 GHz.  Bands such as 2.4 GHz and 5.8 GHz (common for drone control and video) require the HackRF profile and are planned for RF Ragnar v2.

## Next steps

* **RF Ragnar v2** — Publish scan/track/status/error events over MQTT into the SEALS backend.
* **Dash control panel** — Build a real-time dashboard with control buttons to start/stop scanning, calibrate, and export evidence.
* **HackRF profile** — Support 2.4 GHz/5.8 GHz bands for drone and FPV detection.