#!/usr/bin/env python3
"""
Cron entrypoint for Railway.
Triggers the pipeline via the dashboard's /api/cron/run endpoint,
or runs pipeline_runner.py directly if RAILWAY_PUBLIC_DOMAIN is not set.
"""
import os
import sys
import requests
from pathlib import Path

def main():
    # Try hitting the web service endpoint first
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    cron_secret = os.getenv("CRON_SECRET", "")

    if domain:
        url = f"https://{domain}/api/cron/run"
        print(f"Triggering pipeline via {url}")
        try:
            resp = requests.post(url, headers={"X-Cron-Secret": cron_secret}, timeout=30)
            print(f"Response: {resp.status_code} — {resp.text}")
            return
        except Exception as e:
            print(f"Failed to trigger via API: {e}")
            print("Falling back to direct execution...")

    # Direct execution fallback
    print("Running pipeline_runner.py directly...")
    import subprocess
    result = subprocess.run(
        [sys.executable, "pipeline_runner.py"],
        cwd=str(Path(__file__).resolve().parent),
        timeout=900,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
