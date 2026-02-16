import time
import requests
import win32print
from datetime import datetime

SERVER_BASE = "https://yard-gate-alamo.onrender.com"
PRINT_KEY = "8f4c2a0a9e6b1f7d5c8b2e4a9d3c6f7e1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"
DEVICE_ID = "GATE-PC-01"
PRINTER_NAME = "EPSON_TICKET_USB"

def print_raw(text):
    hprinter = win32print.OpenPrinter(PRINTER_NAME)
    job = win32print.StartDocPrinter(hprinter, 1, ("Yard Gate Ticket", None, "RAW"))
    win32print.StartPagePrinter(hprinter)
    win32print.WritePrinter(hprinter, text.encode("cp850", errors="replace"))
    win32print.EndPagePrinter(hprinter)
    win32print.EndDocPrinter(hprinter)
    win32print.ClosePrinter(hprinter)

def mark_done(job_id, status="DONE", error=None):
    headers = {"X-PRINT-KEY": PRINT_KEY}
    requests.post(
        f"{SERVER_BASE}/api/print/jobs/{job_id}/done",
        json={"status": status, "error": error},
        headers=headers,
        timeout=10
    )

def claim_job():
    headers = {"X-PRINT-KEY": PRINT_KEY}
    r = requests.get(
        f"{SERVER_BASE}/api/print/pending?device_id={DEVICE_ID}",
        headers=headers,
        timeout=10
    )
    if not r.ok:
        return None
    data = r.json()
    return data.get("job")

print("🖨️ Print Agent iniciado...")

while True:
    try:
        job = claim_job()
        if job:
            print(f"Imprimiendo job {job['id']}...")
            try:
                print_raw(job["payload_text"] + "\n\n\n\n")
                mark_done(job["id"], "DONE")
                print("OK")
            except Exception as e:
                mark_done(job["id"], "FAILED", str(e))
                print("ERROR:", e)
        time.sleep(2)
    except Exception as e:
        print("Error general:", e)
        time.sleep(5)
