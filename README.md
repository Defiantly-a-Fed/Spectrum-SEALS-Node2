# SPECTRUM SEALS — Node 2 RAGNAR RF

Node 2 is the RF receiver and evidence-collection lane for the SPECTRUM SEALS project.

This repo component focuses on receive-side SDR work using RTL-SDR / NESDR and HackRF hardware. It is built for local RF observation, sweep capture, spectrum visualization, waterfall evidence, and early RAGNAR-style band-activity scoring.

## Current status

Current milestone:

```text
RAGNAR V4 Autowatch
```

This build adds an automatic RF watch loop and a basic V4 drone-band activity detector.

It is not a simulator. It does not use fake locks. It does not claim a confirmed drone identity from weak evidence.

The detector reports RF activity based on real sweep artifacts.

## Hardware lanes

### SEAL-RTL

Used for RTL-SDR / NESDR evidence.

Primary functions:

```text
rtl_power spectrum sweeps
433 MHz ISM observation
902–928 MHz ISM observation
archived rtl_433 decode review
```

Expected tools:

```text
rtl_test
rtl_power
rtl_433
```

`rtl_433` is optional. If it is missing, the sweep system should still run.

### SEAL-HRF

Used for HackRF evidence.

Primary functions:

```text
hackrf_info device detection
hackrf_sweep wideband capture
915 MHz sweep coverage
2.4 GHz sweep coverage
5.8 GHz sweep coverage
```

Expected tools:

```text
hackrf_info
hackrf_sweep
```

## Basic V4 drone-band detector

The V4 detector watches these bands:

```text
433 MHz ISM
902–928 MHz ISM / control-adjacent
2.4 GHz control/video-adjacent
5.8 GHz FPV/video-adjacent
```

The detector scores real sweep artifacts using:

```text
peak-over-median relative SNR
band activity level
sweep persistence proxy
multi-band activity bonus
```

It reports RF activity, not confirmed drone identity.

A stronger future detector can add protocol signatures, timing fingerprints, controller profiles, baseline subtraction, and known-airframe comparison.

## What this version proves

This version proves:

```text
RTL-SDR hardware can be detected
HackRF hardware can be detected
real sweep artifacts can be collected
spectrum and waterfall visuals can be generated
RF activity can be ranked across drone-relevant bands
evidence can be exported for review
```

This version does not prove:

```text
confirmed drone identity
airframe classification
controller fingerprinting
direction finding
range estimation
```

## Windows run instructions

Open PowerShell in the extracted project folder.

Allow local scripts for this PowerShell session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Start the current autowatch build:

```powershell
.\run_windows_v12.ps1
```

Open the local UI:

```text
http://127.0.0.1:8069/app
```

Default login:

```text
admin
seals-local-admin
```

## Recommended operating order

Use this order during testing:

```text
1. Start the app.
2. Open the local UI.
3. Log in.
4. Click Detect SDRs.
5. Confirm RTL-SDR status.
6. Confirm HackRF status.
7. Let RF Watch update automatically.
8. Use manual capture buttons only when needed.
9. Export evidence JSON before shutting down.
```

## Expected SDR tools

Recommended Windows tool paths:

```text
C:\ProgramData\radioconda\Library\bin\rtl_test.exe
C:\ProgramData\radioconda\Library\bin\rtl_power.exe
C:\ProgramData\radioconda\Library\bin\hackrf_info.exe
C:\ProgramData\radioconda\Library\bin\hackrf_sweep.exe
```

If tools are not in PATH, place them in the project tools folder.

Expected local folders:

```text
tools\rtl_sdr\bin
tools\hackrf\bin
evidence
logs
```

## Evidence output

Captured artifacts should be written under:

```text
evidence/
```

Expected evidence files include:

```text
rtl_power CSV sweeps
hackrf_sweep CSV sweeps
device detection reports
capture transcripts
exported evidence JSON
```

## Troubleshooting

If HackRF is detected but sweeps fail, check:

```text
hackrf_sweep exists
HackRF drivers are installed
the HackRF is not already in use by another program
the antenna is connected
the sweep command is in PATH
```

If RTL-SDR is detected but sweeps fail, check:

```text
rtl_power exists
WinUSB driver is installed
the RTL-SDR is not already in use
the antenna is connected
the sweep range is supported by the dongle
```

If `rtl_433` is missing, the UI should not fail. Only live decode capture is unavailable.

## Project rule

Node 2 must stay real-evidence focused.

No simulated lock should be presented as RF proof.

No detector should claim a confirmed drone unless the evidence supports that claim.
