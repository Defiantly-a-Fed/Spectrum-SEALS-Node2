# Spectrum SEALS Node 2

This repository contains Node 2 code and evidence for the Spectrum SEALS project.

It will serve as a forked workspace for ongoing development and testing of the Node 2 receiver and RF Ragnar components.

 
## Updates (June 21, 2026)

This repository now includes the **RAGNAR V4 Autowatch Build**. Key improvements:

- **Real‑only operation.** The UI no longer references simulators or fake locks; all data comes from your RTL‑SDR and HackRF hardware.
- **Basic drone‑band detector.** Searches for RF energy peaks in the 433 MHz, 915 MHz, 2.4 GHz and 5.8 GHz bands using peak‑over‑median SNR. It reports RF activity rather than declaring a definite drone.
- **Automatic RF watch.** The application performs sweeps and updates detection results automatically after startup. Manual capture buttons remain available for on‑demand sweeps.
- **Improved UI.** Cleaner labels, separate lanes for RTL‑SDR and HackRF, and clear status messages when tools are missing.
- **Graceful degradation.** If `rtl_433` or any sweep tool is missing, the application disables that feature without crashing.

### How to run

1. **Extract the archive** (e.g., `spectrum_seals_node2_ragnar_v4_v12_autowatch.zip`) into a working directory.
2. **Install dependencies** if necessary: `pip install -r requirements.txt`.
3. **Start the server**:
   - On Windows, open PowerShell in the extracted directory and run:
     ```
     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
     .\run_windows_v12.ps1
     ```
   - On Linux, run:
     ```
     bash run_linux.sh
     ```
   The server binds to `127.0.0.1` on the port specified in the script (default `8069`).
4. **Open your browser** to `http://127.0.0.1:8069/app` and log in with the credentials defined in `config.py` (`admin`/`seals-local-admin` by default).
5. **Monitor and capture**:
   - Use *Detect SDRs* to confirm that your RTL‑SDR and HackRF devices are recognized.
   - The RF watch runs automatically; you can also trigger manual sweeps for each band using the buttons on the RTL and HackRF lanes.
   - Captured evidence files are stored in the `evidence/` directory.
