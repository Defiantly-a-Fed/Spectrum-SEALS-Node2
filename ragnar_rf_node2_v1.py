"""RF Ragnar Node 2 calibrated detector (Version 1)

This script implements a passive RF detector for the Spectrum SEALS Node 2 using
the RTL‑SDR (Nooelec NESDR SMArt v5). It captures raw I/Q samples, performs
FFT analysis, computes baseline noise levels, evaluates peaks using z‑scores,
and assigns confidence levels to potential signals across multiple ISM bands.
Results are logged in JSONL format, and a simple HTML dashboard is generated
showing the current state for each band.

The calibrated detector works in two phases:

1. **Calibration**: The receiver learns the ambient noise floor for each band
   by capturing several measurement cycles without any deliberate transmissions.
   It computes the median noise floor and peak‑over‑floor levels to set a
   baseline.

2. **Live Scan**: After calibration, the detector repeatedly captures I/Q
   frames for each band, computes the power spectrum, identifies peaks, and
   compares them to the baseline using median absolute deviation (MAD) to
   compute a z‑score. Peaks exceeding a configurable z‑score threshold and
   deviating from the baseline by at least a few dB are treated as abnormal.
   Confidence scores accumulate over time, and the state transitions through
   SCAN, TRACK, and LOCK as confidence grows.

Evidence from each scan is recorded in `logs/ragnar_rf_v1_events.jsonl`, and
human‑readable summaries are saved to `visuals/ragnar_rf_v1_summary.json` and
`visuals/ragnar_rf_v1_operator.html`.

Prerequisites:
    pip install numpy

Usage example:
    python ragnar_rf_node2_v1.py --cycles 12 --calibration-cycles 3 \
        --capture-seconds 0.45

"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


@dataclass
class Band:
    """Configuration and state for an individual frequency band."""

    name: str
    center_hz: int
    sample_rate: int
    gain_db: float
    z_lock_threshold: float
    confidence: float = 0.0
    baseline_floor_db: float = 0.0
    baseline_peak_over_floor_db: float = 0.0
    baseline_ready: bool = False


@dataclass
class Result:
    """Container for a single scan result."""

    timestamp_utc: str
    band: str
    center_hz: int
    peak_hz: float
    peak_mhz: float
    noise_floor_db: float
    peak_power_db: float
    peak_over_floor_db: float
    baseline_floor_db: float
    baseline_peak_over_floor_db: float
    delta_peak_db: float
    activity_percent: float
    z_score: float
    confidence: float
    state: str
    iq_bytes: int
    fft_frames: int


class CalibratedRFRagnar:
    """Calibrated RF detector that runs on a Nooelec NESDR (RTL‑SDR)."""

    def __init__(
        self,
        root: Path,
        rtl_sdr: Path,
        cycles: int,
        calibration_cycles: int,
        capture_seconds: float,
        fft_size: int,
        hop: int,
    ) -> None:
        self.root = root
        self.rtl_sdr = rtl_sdr
        self.cycles = cycles
        self.calibration_cycles = calibration_cycles
        self.capture_seconds = capture_seconds
        self.fft_size = fft_size
        self.hop = hop

        # Working directories
        self.logs = root / "logs"
        self.visuals = root / "visuals"
        self.tmp = root / "tmp"
        for p in [self.logs, self.visuals, self.tmp]:
            p.mkdir(parents=True, exist_ok=True)

        # Output files
        self.event_log = self.logs / "ragnar_rf_v1_events.jsonl"
        self.summary_json = self.visuals / "ragnar_rf_v1_summary.json"
        self.operator_html = self.visuals / "ragnar_rf_v1_operator.html"
        self.baseline_json = self.visuals / "ragnar_rf_v1_baseline.json"

        # Define the bands to scan
        self.bands = [
            Band("315MHz_ISM", 315_000_000, 1_024_000, 32.8, 3.2),
            Band("433MHz_ISM", 433_920_000, 1_024_000, 32.8, 3.2),
            Band("868MHz_ISM", 868_000_000, 1_024_000, 32.8, 3.0),
            Band("915MHz_ISM", 915_000_000, 1_024_000, 32.8, 3.0),
            Band("1090MHz_ADSB", 1_090_000_000, 1_024_000, 32.8, 2.7),
        ]

    def capture(self, band: Band) -> Path:
        """Capture raw I/Q samples for a single band using rtl_sdr."""
        samples = max(int(band.sample_rate * self.capture_seconds), self.fft_size * 8)
        out = self.tmp / f"ragnar_v1_{band.name}.u8"
        if out.exists():
            out.unlink()

        cmd = [
            str(self.rtl_sdr),
            "-d",
            "0",
            "-f",
            str(band.center_hz),
            "-s",
            str(band.sample_rate),
            "-g",
            str(band.gain_db),
            "-n",
            str(samples),
            str(out),
        ]

        proc = subprocess.run(
            cmd,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(10, int(self.capture_seconds + 8)),
            check=False,
        )

        stderr_path = self.logs / f"ragnar_v1_{band.name}_rtl_sdr_stderr.txt"
        stderr_path.write_text(proc.stderr, encoding="utf-8", errors="replace")

        # Validate output
        if not out.exists() or out.stat().st_size < self.fft_size * 2:
            raise RuntimeError(f"I/Q capture failed for {band.name}; see {stderr_path}")

        return out

    def analyze(self, band: Band, iq_path: Path) -> dict:
        """Analyze captured I/Q and return metrics for the spectrum."""
        raw = np.fromfile(iq_path, dtype=np.uint8)
        if raw.size % 2:
            raw = raw[:-1]

        # Deinterleave and normalize
        i = raw[0::2].astype(np.float32) - 127.5
        q = raw[1::2].astype(np.float32) - 127.5
        iq = (i + 1j * q) / 127.5
        iq = iq - np.mean(iq)

        frames = []
        window = np.hanning(self.fft_size).astype(np.float32)

        # Sliding FFT
        for start in range(0, len(iq) - self.fft_size, self.hop):
            chunk = iq[start : start + self.fft_size]
            spectrum = np.fft.fftshift(np.fft.fft(chunk * window))
            power_db = 20.0 * np.log10(np.abs(spectrum) + 1e-12)
            frames.append(power_db)

        if not frames:
            raise RuntimeError(f"Not enough samples for FFT: {band.name}")

        waterfall = np.array(frames, dtype=np.float32)
        avg = waterfall.mean(axis=0)

        offsets = np.fft.fftshift(np.fft.fftfreq(self.fft_size, d=1.0 / band.sample_rate))
        absolute = band.center_hz + offsets

        # Ignore DC region to avoid bias
        usable_mask = np.abs(offsets) > 18_000
        usable_power = avg[usable_mask]
        usable_freqs = absolute[usable_mask]

        floor = float(np.median(usable_power))
        mad = float(np.median(np.abs(usable_power - floor))) + 1e-6

        peak_idx = int(np.argmax(usable_power))
        peak_power = float(usable_power[peak_idx])
        peak_hz = float(usable_freqs[peak_idx])
        peak_over_floor = float(peak_power - floor)
        z = float((peak_power - floor) / max(mad * 1.4826, 1e-6))

        activity_percent = float(np.mean(usable_power > floor + 6.0) * 100.0)

        return {
            "iq_bytes": int(raw.size),
            "fft_frames": int(waterfall.shape[0]),
            "peak_hz": peak_hz,
            "peak_mhz": peak_hz / 1_000_000.0,
            "noise_floor_db": floor,
            "peak_power_db": peak_power,
            "peak_over_floor_db": peak_over_floor,
            "mad_db": mad,
            "z_score": z,
            "activity_percent": activity_percent,
        }

    def write_jsonl(self, obj: dict) -> None:
        """Append an event to the JSONL log."""
        with self.event_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")

    def calibrate(self) -> None:
        """Measure baseline noise floor and peak levels for each band."""
        print()
        print("=== RF RAGNAR V1 CALIBRATION ===")
        print("Keep the RF environment normal. Do not press key fobs yet.")

        baseline = {}
        collected = {b.name: [] for b in self.bands}

        for c in range(self.calibration_cycles):
            print(f"Calibration pass {c + 1}/{self.calibration_cycles}")
            for band in self.bands:
                iq = self.capture(band)
                a = self.analyze(band, iq)
                collected[band.name].append(a)
                print(
                    f"  {band.name:<12} floor={a['noise_floor_db']:.2f} "
                    f"p/f={a['peak_over_floor_db']:.2f} z={a['z_score']:.2f}"
                )

        for band in self.bands:
            rows = collected[band.name]
            band.baseline_floor_db = float(np.median([r["noise_floor_db"] for r in rows]))
            band.baseline_peak_over_floor_db = float(
                np.median([r["peak_over_floor_db"] for r in rows])
            )
            band.baseline_ready = True
            baseline[band.name] = {
                "center_hz": band.center_hz,
                "sample_rate": band.sample_rate,
                "gain_db": band.gain_db,
                "baseline_floor_db": band.baseline_floor_db,
                "baseline_peak_over_floor_db": band.baseline_peak_over_floor_db,
                "z_lock_threshold": band.z_lock_threshold,
            }

        payload = {
            "project": "SPECTRUM_SEALS",
            "node": "Node 2",
            "system": "RF Ragnar v1 calibrated",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "baseline": baseline,
        }
        self.baseline_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Baseline saved: {self.baseline_json}")

    def scan_once(self) -> list[Result]:
        """Perform a single scan across all bands and return results."""
        results: list[Result] = []

        for band in self.bands:
            iq = self.capture(band)
            a = self.analyze(band, iq)

            delta_peak = a["peak_over_floor_db"] - band.baseline_peak_over_floor_db
            abnormal = (
                a["z_score"] >= band.z_lock_threshold
                and delta_peak >= 2.0
                and a["peak_over_floor_db"] >= 4.5
            )

            # Raw score influences confidence accumulation
            raw_score = min(
                100.0,
                max(
                    0.0,
                    (a["z_score"] / 8.0) * 55.0
                    + delta_peak * 5.0
                    + min(a["activity_percent"], 20.0),
                ),
            )

            if abnormal:
                band.confidence = min(100.0, band.confidence * 0.72 + raw_score * 0.48)
            else:
                band.confidence = max(0.0, band.confidence * 0.55)

            # Determine state based on confidence
            if band.confidence >= 65.0:
                state = "LOCK"
            elif band.confidence >= 25.0:
                state = "TRACK"
            else:
                state = "SCAN"

            result = Result(
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                band=band.name,
                center_hz=band.center_hz,
                peak_hz=a["peak_hz"],
                peak_mhz=a["peak_mhz"],
                noise_floor_db=a["noise_floor_db"],
                peak_power_db=a["peak_power_db"],
                peak_over_floor_db=a["peak_over_floor_db"],
                baseline_floor_db=band.baseline_floor_db,
                baseline_peak_over_floor_db=band.baseline_peak_over_floor_db,
                delta_peak_db=delta_peak,
                activity_percent=a["activity_percent"],
                z_score=a["z_score"],
                confidence=band.confidence,
                state=state,
                iq_bytes=a["iq_bytes"],
                fft_frames=a["fft_frames"],
            )

            event = {
                "project": "SPECTRUM_SEALS",
                "node_id": "node2_rf_ragnar",
                "event_type": "rf_ragnar_v1_scan",
                **result.__dict__,
            }
            self.write_jsonl(event)
            results.append(result)

        return results

    def render(self, results: list[Result]) -> None:
        """Render the results to an HTML file and save summary JSON."""
        best = max(results, key=lambda r: r.confidence) if results else None

        rows = []
        for r in results:
            rows.append(
                f"""
<tr>
<td>{r.band}</td>
<td class=\"{r.state.lower()}\">{r.state}</td>
<td>{r.peak_mhz:.6f}</td>
<td>{r.peak_over_floor_db:.2f}</td>
<td>{r.delta_peak_db:+.2f}</td>
<td>{r.z_score:.2f}</td>
<td>{r.activity_percent:.2f}%</td>
<td>{r.confidence:.1f}%</td>
<td>{r.iq_bytes:,}</td>
<td>{r.timestamp_utc}</td>
</tr>
"""
            )

        best_text = "none"
        if best:
            best_text = (
                f"{best.band} @ {best.peak_mhz:.6f} MHz / "
                f"{best.confidence:.1f}% / {best.state}"
            )

        html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>SPECTRUM SEALS RF Ragnar v1</title>
<style>
body {{
  background: #060a10;
  color: #eaf1ff;
  font-family: Consolas, monospace;
  margin: 0;
}}
.header {{
  padding: 24px;
  background: #101722;
  border-bottom: 1px solid #344055;
}}
h1 {{
  margin: 0 0 8px 0;
  letter-spacing: 1px;
}}
.sub {{
  color: #b7c6dc;
}}
.best {{
  margin-top: 18px;
  font-size: 22px;
  font-weight: 800;
}}
.panel {{
  padding: 18px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
}}
th, td {{
  border-bottom: 1px solid #273246;
  padding: 10px;
  text-align: left;
}}
th {{
  color: #c8d9f2;
}}
.scan {{ color: #aab7c8; }}
.track {{ color: #ffd166; font-weight: 800; }}
.lock {{ color: #7CFF9B; font-weight: 900; }}
.footer {{
  color: #8fa1ba;
  padding: 18px;
}}
</style>
</head>
<body>
<div class="header">
<h1>SPECTRUM SEALS — RF RAGNAR NODE 2 v1</h1>
<div class="sub">Calibrated passive RTL-SDR I/Q hunter • baseline floor • z-score peaks • confidence tracker • JSONL evidence</div>
<div class="best">Best current track: {best_text}</div>
</div>
<div class="panel">
<table>
<thead>
<tr>
<th>Band</th>
<th>State</th>
<th>Peak MHz</th>
<th>Peak/Floor dB</th>
<th>Δ Baseline</th>
<th>Z</th>
<th>Activity</th>
<th>Confidence</th>
<th>I/Q Bytes</th>
<th>UTC</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</div>
<div class="footer">
Baseline: {self.baseline_json}<br>
Evidence log: {self.event_log}<br>
Summary: {self.summary_json}
</div>
</body>
</html>
"""

        self.operator_html.write_text(html, encoding="utf-8")

        summary = {
            "project": "SPECTRUM_SEALS",
            "node": "Node 2",
            "system": "RF Ragnar v1 calibrated",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "receiver": "Nooelec NESDR SMArt v5 / RTL2832U + R820T",
            "best_track": best.__dict__ if best else None,
            "results": [r.__dict__ for r in results],
            "operator_html": str(self.operator_html),
            "event_log": str(self.event_log),
        }
        self.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def print_table(self, idx: int, results: list[Result]) -> None:
        """Print a human-readable table to stdout."""
        print()
        print(f"=== RF RAGNAR V1 CYCLE {idx + 1}/{self.cycles} ===")
        print(
            f"{'BAND':<14} {'STATE':<7} {'PEAK MHz':>12} {'P/F':>7} {'DELTA':>8} "
            f"{'Z':>7} {'ACT':>8} {'CONF':>8}"
        )
        for r in results:
            print(
                f"{r.band:<14} {r.state:<7} {r.peak_mhz:>12.6f} "
                f"{r.peak_over_floor_db:>7.2f} {r.delta_peak_db:>+8.2f} "
                f"{r.z_score:>7.2f} {r.activity_percent:>7.2f}% {r.confidence:>7.1f}%"
            )
        print(f"HTML: {self.operator_html}")

    def run(self) -> None:
        """Execute calibration and scanning cycles."""
        self.calibrate()

        print()
        print("=== RF RAGNAR V1 LIVE HUNT ===")
        print(
            "Now cause a legal local signal if you have one: car key fob, 433 weather sensor, "
            "garage remote, ADS-B nearby, etc. Press Ctrl+C to stop after the current capture."
        )
        latest: list[Result] = []

        try:
            for i in range(self.cycles):
                latest = self.scan_once()
                self.render(latest)
                self.print_table(i, latest)
        except KeyboardInterrupt:
            print("Stopped by operator.")
            if latest:
                self.render(latest)


def find_rtl_sdr(root: Path) -> Path:
    """Find rtl_sdr executable in the project or system path."""
    local = list((root / "bin").rglob("rtl_sdr.exe"))
    if local:
        return local[0]
    path = shutil.which("rtl_sdr")
    if path:
        return Path(path)
    raise FileNotFoundError(
        f"rtl_sdr.exe not found. Put rtl_sdr.exe and DLLs in {root / 'bin'}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--calibration-cycles", type=int, default=3)
    parser.add_argument("--capture-seconds", type=float, default=0.45)
    parser.add_argument("--fft-size", type=int, default=2048)
    parser.add_argument("--hop", type=int, default=1024)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    rtl = find_rtl_sdr(root)

    app = CalibratedRFRagnar(
        root=root,
        rtl_sdr=rtl,
        cycles=args.cycles,
        calibration_cycles=args.calibration_cycles,
        capture_seconds=args.capture_seconds,
        fft_size=args.fft_size,
        hop=args.hop,
    )
    app.run()


if __name__ == "__main__":
    main()