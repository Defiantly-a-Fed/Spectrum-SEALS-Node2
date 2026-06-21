# Node 2 RAGNAR RF README

This file is kept for compatibility with the original Node 2 RAGNAR V1 filename.

The current maintained documentation is in:

```text
README.md
```

The active detector implementation is:

```text
ragnar_rf_node2_v1.py
```

## Current build

The current build is the SPECTRUM SEALS Node 2 RAGNAR V4 Autowatch build.

It supports:

* RTL-SDR / NESDR sweep evidence through `rtl_power`
* HackRF sweep evidence through `hackrf_sweep`
* Automatic RF watch mode
* Basic drone-band RF activity scoring
* Evidence export
* Real-only operation with no simulator locks

## Important note

This tool reports RF-band activity from real sweep artifacts.

It does not claim confirmed drone identity without stronger evidence.

For full setup, run instructions, troubleshooting, and operating notes, use the main repository README.
