#!/usr/bin/env python3
"""
SPECTRUM SEALS - Node 2 RAGNAR RF
Real-only V4 autowatch / evidence collector for RTL-SDR + HackRF.

This file replaces the old RTL-only raw I/Q V1 script while keeping the original
filename for repo continuity. It does not simulate RF activity and it does not
claim a confirmed drone identity from weak evidence. It captures real sweep CSV
artifacts, scores band activity, writes JSONL evidence, and renders a local HTML
operator page.

Supported lanes:
  - SEAL-RTL: rtl_test + rtl_power + optional rtl_433
  - SEAL-HRF: hackrf_info + hackrf_sweep

Typical Windows run:
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  python .\ragnar_rf_node2_v1.py --watch --cycles 3

Typical quick checks:
  python .\ragnar_rf_node2_v1.py --detect
  python .\ragnar_rf_node2_v1.py --capture-once

Outputs:
  evidence/ragnar_v4_events.jsonl
  evidence/ragnar_v4_summary.json
  evidence/ragnar_v4_operator.html
  evidence/*.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Iterable

PROJECT = "SPECTRUM_SEALS"
NODE_ID = "node2_ragnar_rf"
SYSTEM = "RAGNAR_V4_AUTOWATCH_REAL_ONLY"


@dataclass(frozen=True)
class SweepBand:
    key: str
    label: str
    lane: str
    tool: str
    freq_arg: str
    start_mhz: float
    stop_mhz: float
    step_hz: int
    seconds: int
    threshold_db: float


@dataclass
class ToolStatus:
    name: str
    path: str | None
    found: bool
    detected: bool | None = None
    detail: str = ""


@dataclass
class SweepMetrics:
    band_key: str
    label: str
    lane: str
    tool: str
    csv_path: str | None
    timestamp_utc: str
    bins: int
    rows: int
    peak_mhz: float | None
    peak_db: float | None
    median_db: float | None
    rel_snr_db: float | None
    activity_percent: float
    status: str
    score: float
    meaning: str
    sha256: str | None = None
    error: str | None = None


@dataclass
class RunState:
    started_utc: str
    mode: str
    tools: dict[str, ToolStatus] = field(default_factory=dict)
    sweeps: dict[str, SweepMetrics] = field(default_factory=dict)
    capture_status: str = "IDLE"
    last_message: str = "ready"


BANDS: list[SweepBand] = [
    SweepBand(
        key="rtl_433",
        label="433 MHz ISM",
        lane="SEAL-RTL",
        tool="rtl_power",
        freq_arg="420M:450M:100k",
        start_mhz=420.0,
        stop_mhz=450.0,
        step_hz=100_000,
        seconds=10,
        threshold_db=8.0,
    ),
    SweepBand(
        key="rtl_915",
        label="902-928 MHz ISM",
        lane="SEAL-RTL",
        tool="rtl_power",
        freq_arg="902M:928M:100k",
        start_mhz=902.0,
        stop_mhz=928.0,
        step_hz=100_000,
        seconds=10,
        threshold_db=8.0,
    ),
    SweepBand(
        key="hrf_915",
        label="902-928 MHz HackRF",
        lane="SEAL-HRF",
        tool="hackrf_sweep",
        freq_arg="902:928",
        start_mhz=902.0,
        stop_mhz=928.0,
        step_hz=250_000,
        seconds=10,
        threshold_db=8.0,
    ),
    SweepBand(
        key="hrf_24",
        label="2.4 GHz control/video-adjacent",
        lane="SEAL-HRF",
        tool="hackrf_sweep",
        freq_arg="2400:2500",
        start_mhz=2400.0,
        stop_mhz=2500.0,
        step_hz=1_000_000,
        seconds=10,
        threshold_db=7.0,
    ),
    SweepBand(
        key="hrf_58",
        label="5.8 GHz FPV/video-adjacent",
        lane="SEAL-HRF",
        tool="hackrf_sweep",
        freq_arg="5650:5925",
        start_mhz=5650.0,
        stop_mhz=5925.0,
        step_hz=1_000_000,
        seconds=10,
        threshold_db=7.0,
    ),
]


class RFRagnarV4:
    def __init__(self, root: Path, evidence_dir: Path) -> None:
        self.root = root.resolve()
        self.evidence = evidence_dir.resolve()
        self.logs = self.evidence / "logs"
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.state = RunState(
            started_utc=utc_now(),
            mode=SYSTEM,
            capture_status="IDLE",
            last_message="ready",
        )
        self.events_jsonl = self.evidence / "ragnar_v4_events.jsonl"
        self.summary_json = self.evidence / "ragnar_v4_summary.json"
        self.operator_html = self.evidence / "ragnar_v4_operator.html"

    def write_event(self, event_type: str, payload: dict) -> None:
        event = {
            "timestamp_utc": utc_now(),
            "project": PROJECT,
            "node_id": NODE_ID,
            "system": SYSTEM,
            "event_type": event_type,
            **payload,
        }
        with self.events_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def find_tool(self, name: str) -> str | None:
        candidates: list[Path] = []
        suffixes = [".exe", ""] if os.name == "nt" else ["", ".exe"]
        for base in [
            self.root,
            self.root / "bin",
            self.root / "tools",
            self.root / "tools" / "rtl_sdr" / "bin",
            self.root / "tools" / "rtl_433" / "bin",
            self.root / "tools" / "hackrf" / "bin",
            Path("C:/ProgramData/radioconda/Library/bin"),
        ]:
            for suffix in suffixes:
                candidates.append(base / f"{name}{suffix}")
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)
        found = shutil.which(name)
        if found:
            return found
        if os.name == "nt":
            found_exe = shutil.which(f"{name}.exe")
            if found_exe:
                return found_exe
        return None

    def detect_tools(self) -> dict[str, ToolStatus]:
        names = ["rtl_test", "rtl_power", "rtl_433", "hackrf_info", "hackrf_sweep"]
        result: dict[str, ToolStatus] = {}
        for name in names:
            path = self.find_tool(name)
            result[name] = ToolStatus(name=name, path=path, found=bool(path))

        if result["rtl_test"].found:
            detail, rc = self.run_command([result["rtl_test"].path or "rtl_test", "-t"], timeout=12)
            result["rtl_test"].detail = trim(detail, 3000)
            result["rtl_test"].detected = "Found" in detail or "Using device" in detail or "R820T" in detail or rc == 0

        if result["hackrf_info"].found:
            detail, rc = self.run_command([result["hackrf_info"].path or "hackrf_info"], timeout=12)
            result["hackrf_info"].detail = trim(detail, 3000)
            result["hackrf_info"].detected = "Found HackRF" in detail or "HackRF One" in detail or rc == 0

        self.state.tools = result
        self.state.last_message = "device detection complete"
        self.write_event("device_detect", {"tools": {k: asdict(v) for k, v in result.items()}})
        self.save_summary()
        return result

    def capture_band(self, band: SweepBand) -> SweepMetrics:
        self.state.capture_status = f"CAPTURING {band.key}"
        self.state.last_message = f"starting {band.label}"
        self.save_summary()

        tool_path = self.find_tool(band.tool)
        if not tool_path:
            metrics = SweepMetrics(
                band_key=band.key,
                label=band.label,
                lane=band.lane,
                tool=band.tool,
                csv_path=None,
                timestamp_utc=utc_now(),
                bins=0,
                rows=0,
                peak_mhz=None,
                peak_db=None,
                median_db=None,
                rel_snr_db=None,
                activity_percent=0.0,
                status="TOOL_MISSING",
                score=0.0,
                meaning=f"{band.tool} not found; no claim for this band.",
                error=f"{band.tool} executable not found",
            )
            self.state.sweeps[band.key] = metrics
            self.write_event("capture_error", asdict(metrics))
            self.state.capture_status = "IDLE"
            self.state.last_message = metrics.error or "capture error"
            self.render()
            self.save_summary()
            return metrics

        out = self.evidence / f"{utc_slug()}_{band.key}.csv"
        if band.tool == "rtl_power":
            cmd = [tool_path, "-f", band.freq_arg, "-i", "1s", "-e", f"{band.seconds}s", str(out)]
        elif band.tool == "hackrf_sweep":
            # Frequencies are MHz for hackrf_sweep. -N limits sweeps when supported.
            cmd = [tool_path, "-f", band.freq_arg, "-w", str(band.step_hz), "-l", "32", "-g", "20", "-N", str(max(3, band.seconds)), "-r", str(out)]
        else:
            raise ValueError(f"Unsupported tool: {band.tool}")

        transcript, rc = self.run_command(cmd, timeout=max(20, band.seconds + 20))
        transcript_path = self.logs / f"{out.stem}_{band.tool}_transcript.txt"
        transcript_path.write_text(transcript, encoding="utf-8", errors="replace")

        if rc != 0 or not out.exists() or out.stat().st_size < 20:
            metrics = SweepMetrics(
                band_key=band.key,
                label=band.label,
                lane=band.lane,
                tool=band.tool,
                csv_path=str(out) if out.exists() else None,
                timestamp_utc=utc_now(),
                bins=0,
                rows=0,
                peak_mhz=None,
                peak_db=None,
                median_db=None,
                rel_snr_db=None,
                activity_percent=0.0,
                status="CAPTURE_FAILED",
                score=0.0,
                meaning="No usable sweep artifact was written; no claim for this band.",
                error=f"return={rc}; transcript={transcript_path}",
            )
            self.state.sweeps[band.key] = metrics
            self.write_event("capture_error", asdict(metrics))
            self.state.capture_status = "IDLE"
            self.state.last_message = metrics.error or "capture failed"
            self.render()
            self.save_summary()
            return metrics

        metrics = self.analyze_sweep_csv(band, out)
        self.state.sweeps[band.key] = metrics
        self.write_event("sweep_capture", asdict(metrics))
        self.state.capture_status = "IDLE"
        self.state.last_message = f"captured {band.label}: {metrics.status}"
        self.render()
        self.save_summary()
        return metrics

    def capture_once(self) -> list[SweepMetrics]:
        self.detect_tools()
        results: list[SweepMetrics] = []
        for band in BANDS:
            tool = self.state.tools.get(band.tool)
            if tool and tool.found:
                results.append(self.capture_band(band))
            else:
                results.append(self.capture_band(band))
        return results

    def watch(self, cycles: int, interval_seconds: int) -> None:
        self.detect_tools()
        self.render()
        for idx in range(cycles):
            print(f"\n=== RAGNAR V4 AUTOWATCH CYCLE {idx + 1}/{cycles} ===")
            results = self.capture_once()
            print(self.format_console(results))
            if idx < cycles - 1:
                time.sleep(max(1, interval_seconds))

    def analyze_sweep_csv(self, band: SweepBand, path: Path) -> SweepMetrics:
        values: list[tuple[float, float]] = []
        rows = 0
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                parsed = parse_sweep_row(row)
                if not parsed:
                    continue
                rows += 1
                values.extend(parsed)

        if not values:
            return SweepMetrics(
                band_key=band.key,
                label=band.label,
                lane=band.lane,
                tool=band.tool,
                csv_path=str(path),
                timestamp_utc=utc_now(),
                bins=0,
                rows=rows,
                peak_mhz=None,
                peak_db=None,
                median_db=None,
                rel_snr_db=None,
                activity_percent=0.0,
                status="PARSE_FAILED",
                score=0.0,
                meaning="CSV existed but no sweep bins were parsed; no claim.",
                sha256=sha256_file(path),
                error="no usable bins parsed",
            )

        powers = [p for _, p in values]
        med = median(powers)
        peak_freq, peak_power = max(values, key=lambda item: item[1])
        rel = peak_power - med
        active_bins = sum(1 for p in powers if p > med + 6.0)
        activity_percent = 100.0 * active_bins / max(1, len(powers))

        score = clamp((rel / 16.0) * 70.0 + min(activity_percent, 25.0), 0.0, 100.0)
        if rel >= band.threshold_db + 8.0:
            status = "HIGH_RF_ACTIVITY"
            meaning = "Strong real RF activity in this band. Treat as signal evidence, not identity proof."
        elif rel >= band.threshold_db:
            status = "RF_ACTIVITY"
            meaning = "Real RF activity detected above the local median floor."
        else:
            status = "QUIET"
            meaning = "No strong activity above the detector threshold."

        return SweepMetrics(
            band_key=band.key,
            label=band.label,
            lane=band.lane,
            tool=band.tool,
            csv_path=str(path),
            timestamp_utc=utc_now(),
            bins=len(values),
            rows=rows,
            peak_mhz=peak_freq / 1_000_000.0,
            peak_db=peak_power,
            median_db=med,
            rel_snr_db=rel,
            activity_percent=activity_percent,
            status=status,
            score=score,
            meaning=meaning,
            sha256=sha256_file(path),
            error=None,
        )

    def save_summary(self) -> None:
        payload = {
            "project": PROJECT,
            "node_id": NODE_ID,
            "system": SYSTEM,
            "timestamp_utc": utc_now(),
            "state": {
                "started_utc": self.state.started_utc,
                "mode": self.state.mode,
                "capture_status": self.state.capture_status,
                "last_message": self.state.last_message,
                "tools": {k: asdict(v) for k, v in self.state.tools.items()},
                "sweeps": {k: asdict(v) for k, v in self.state.sweeps.items()},
            },
            "operator_html": str(self.operator_html),
            "events_jsonl": str(self.events_jsonl),
        }
        self.summary_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def render(self) -> None:
        best = self.best_sweep()
        tool_rows = []
        for name in ["rtl_test", "rtl_power", "rtl_433", "hackrf_info", "hackrf_sweep"]:
            t = self.state.tools.get(name, ToolStatus(name=name, path=None, found=False))
            detected = "yes" if t.detected is True else "no" if t.detected is False else "n/a"
            tool_rows.append(
                f"<tr><td>{esc(name)}</td><td>{'yes' if t.found else 'no'}</td><td>{detected}</td><td>{esc(t.path or '')}</td></tr>"
            )

        sweep_rows = []
        for band in BANDS:
            m = self.state.sweeps.get(band.key)
            if not m:
                sweep_rows.append(
                    f"<tr><td>{esc(band.label)}</td><td>{esc(band.lane)}</td><td>NO_SWEEP_YET</td><td>—</td><td>—</td><td>—</td><td>No claim.</td></tr>"
                )
                continue
            sweep_rows.append(
                "<tr>"
                f"<td>{esc(m.label)}</td>"
                f"<td>{esc(m.lane)}</td>"
                f"<td class='{css_status(m.status)}'>{esc(m.status)}</td>"
                f"<td>{fmt(m.peak_mhz, 6)}</td>"
                f"<td>{fmt(m.rel_snr_db, 2)}</td>"
                f"<td>{m.score:.1f}%</td>"
                f"<td>{esc(m.meaning)}</td>"
                "</tr>"
            )

        best_text = "No current RF activity track."
        if best:
            best_text = f"{best.label}: {best.status} @ {fmt(best.peak_mhz, 6)} MHz / score {best.score:.1f}%"

        html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>SPECTRUM SEALS</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin:0; background:#07101b; color:#e8eef8; font-family:Inter,Segoe UI,Arial,sans-serif; }}
header {{ padding:22px 26px; border-bottom:1px solid #23344d; background:#0b1422; }}
h1 {{ margin:0; font-size:28px; letter-spacing:.08em; }}
main {{ padding:22px; display:grid; gap:18px; }}
.card {{ background:#0d1828; border:1px solid #20324b; border-radius:14px; padding:18px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
.kpi {{ background:#08111d; border:1px solid #20324b; border-radius:12px; padding:14px; }}
.kpi small {{ display:block; color:#8fb2e5; text-transform:uppercase; font-weight:700; letter-spacing:.12em; }}
.kpi b {{ display:block; margin-top:8px; font-size:20px; }}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ padding:10px; border-bottom:1px solid #20324b; text-align:left; vertical-align:top; }}
th {{ color:#8fb2e5; font-size:12px; text-transform:uppercase; letter-spacing:.1em; }}
.ok {{ color:#7dff9b; font-weight:800; }}
.warn {{ color:#ffd166; font-weight:800; }}
.bad {{ color:#ff7b7b; font-weight:800; }}
.muted {{ color:#8fa1ba; }}
pre {{ white-space:pre-wrap; overflow:auto; background:#050b13; border:1px solid #20324b; border-radius:12px; padding:14px; }}
</style>
</head>
<body>
<header><h1>SPECTRUM SEALS</h1></header>
<main>
<section class="kpis">
<div class="kpi"><small>Mode</small><b>{esc(self.state.mode)}</b></div>
<div class="kpi"><small>Capture</small><b>{esc(self.state.capture_status)}</b></div>
<div class="kpi"><small>Best track</small><b>{esc(best_text)}</b></div>
<div class="kpi"><small>Updated</small><b>{esc(utc_now())}</b></div>
</section>
<section class="card"><h2>Drone-band RF watch</h2><table><thead><tr><th>Band</th><th>Lane</th><th>Status</th><th>Peak MHz</th><th>Rel SNR dB</th><th>Score</th><th>Meaning</th></tr></thead><tbody>{''.join(sweep_rows)}</tbody></table></section>
<section class="card"><h2>Tool and hardware status</h2><table><thead><tr><th>Tool</th><th>Found</th><th>Hardware detected</th><th>Path</th></tr></thead><tbody>{''.join(tool_rows)}</tbody></table></section>
<section class="card"><h2>Latest message</h2><pre>{esc(self.state.last_message)}</pre></section>
<section class="card"><h2>Evidence</h2><pre>Summary: {esc(str(self.summary_json))}\nEvents: {esc(str(self.events_jsonl))}\nHTML: {esc(str(self.operator_html))}</pre></section>
</main>
</body>
</html>
"""
        self.operator_html.write_text(html_doc, encoding="utf-8")

    def best_sweep(self) -> SweepMetrics | None:
        candidates = [m for m in self.state.sweeps.values() if m.score > 0 and m.status not in {"TOOL_MISSING", "CAPTURE_FAILED", "PARSE_FAILED"}]
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.score)

    def format_console(self, results: Iterable[SweepMetrics]) -> str:
        lines = ["BAND                           LANE       STATUS            PEAK MHz      REL SNR   SCORE"]
        for m in results:
            lines.append(
                f"{m.label[:30]:<30} {m.lane:<10} {m.status:<17} {fmt(m.peak_mhz, 6):>10} {fmt(m.rel_snr_db, 2):>9} {m.score:>6.1f}%"
            )
        lines.append(f"HTML: {self.operator_html}")
        return "\n".join(lines)

    def run_command(self, cmd: list[str], timeout: int) -> tuple[str, int]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            combined = f"$ {' '.join(cmd)}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
            return combined, proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            return f"TIMEOUT after {timeout}s\nSTDOUT:\n{out}\nSTDERR:\n{err}", 124
        except OSError as exc:
            return f"OS error running command: {exc}", 127


def parse_sweep_row(row: list[str]) -> list[tuple[float, float]] | None:
    if len(row) < 7:
        return None
    try:
        low = float(row[2].strip())
        high = float(row[3].strip())
        step = float(row[4].strip())
    except ValueError:
        return None
    powers: list[float] = []
    for item in row[6:]:
        try:
            powers.append(float(item.strip()))
        except ValueError:
            continue
    if not powers or step <= 0 or high <= low:
        return None
    return [(low + step * idx, power) for idx, power in enumerate(powers)]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def trim(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "\n...trimmed..."


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def fmt(value: float | None, digits: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def css_status(status: str) -> str:
    if status in {"RF_ACTIVITY", "HIGH_RF_ACTIVITY"}:
        return "warn"
    if status in {"QUIET"}:
        return "ok"
    return "bad"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPECTRUM SEALS Node 2 RAGNAR RF real-only autowatch")
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument("--evidence-dir", default="evidence", help="Evidence output directory")
    parser.add_argument("--detect", action="store_true", help="Only detect tools/devices and exit")
    parser.add_argument("--capture-once", action="store_true", help="Capture all supported bands once and exit")
    parser.add_argument("--watch", action="store_true", help="Run automatic RF watch cycles")
    parser.add_argument("--cycles", type=int, default=1, help="Number of watch cycles")
    parser.add_argument("--interval", type=int, default=5, help="Seconds between watch cycles")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    root = Path(args.root).resolve()
    app = RFRagnarV4(root=root, evidence_dir=root / args.evidence_dir)

    if args.detect:
        tools = app.detect_tools()
        app.render()
        app.save_summary()
        print(json.dumps({k: asdict(v) for k, v in tools.items()}, indent=2))
        print(f"HTML: {app.operator_html}")
        return 0

    if args.capture_once:
        results = app.capture_once()
        print(app.format_console(results))
        return 0

    if args.watch or not (args.detect or args.capture_once):
        app.watch(cycles=max(1, args.cycles), interval_seconds=max(1, args.interval))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
