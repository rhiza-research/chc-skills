# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "gcsfs",
#   "paramiko>=3",
# ]
# ///
"""Copy recent KMSA WRF runs from the NWP workstation to gs://sheerwater-datalake/kmsa-wrf.

Reads KMSA_SERVER (user@host[:port]) and KMSA_SECRET (that user's password) from the
environment, or asks for them when run in a terminal without them, lists /home/nwp1/LIBS2/{7_day,Daily_Data} over SFTP, and uploads each run
file from the last --days days that is missing from the bucket or a different size
there (a run still being written is re-copied on the next sync). Files changed in the
last --settle-minutes are left for the next sync. GCS write access comes from
Application Default Credentials.

    uv run tools/kmsa_wrf_sync.py [--days 10] [--dry-run]
"""

import argparse
import getpass
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import gcsfs
import paramiko

SOURCE_ROOT = "/home/nwp1/LIBS2"
BUCKET_ROOT = "sheerwater-datalake/kmsa-wrf"
FOLDERS = ("7_day", "Daily_Data")
FILE_RE = re.compile(r"^(\d{8})_to_(\d{8})_fcst\.nc$")
SERVER_RE = re.compile(r"^(?P<user>[^@\s]+)@(?P<host>[^:\s]+)(?::(?P<port>\d+))?$")

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--days", type=int, default=10, help="Look back this many days (default 10).")
parser.add_argument("--settle-minutes", type=int, default=30)
parser.add_argument("--dry-run", action="store_true", help="List what would be copied.")
args = parser.parse_args()

server = os.environ.get("KMSA_SERVER", "").strip()
secret = os.environ.get("KMSA_SECRET", "")
# Run by hand: ask for whatever the environment doesn't give (the password is hidden).
if sys.stdin.isatty():
    if not server:
        server = input("KMSA server (user@host:port): ").strip()
    if not secret:
        secret = getpass.getpass("KMSA secret (password): ")
m = SERVER_RE.match(server)
if not m or not secret:
    sys.exit("KMSA_SERVER (user@host[:port]) and KMSA_SECRET (password) must be set.")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.WarningPolicy())
client.connect(
    m["host"],
    port=int(m["port"] or 22),
    username=m["user"],
    password=secret,
    timeout=30,
    allow_agent=False,
    look_for_keys=False,
)
sftp = client.open_sftp()
gcs = gcsfs.GCSFileSystem()

oldest = (datetime.now(UTC).date() - timedelta(days=args.days)).strftime("%Y%m%d")
settled_before = time.time() - args.settle_minutes * 60
copied = skipped = 0
for folder in FOLDERS:
    dest = f"{BUCKET_ROOT}/{folder}"
    in_bucket = {
        info["name"].rsplit("/", 1)[-1]: int(info["size"])
        for info in (gcs.ls(dest, detail=True) if gcs.exists(dest) else [])
    }
    for entry in sftp.listdir_attr(f"{SOURCE_ROOT}/{folder}"):
        name = entry.filename
        fm = FILE_RE.match(name)
        if not fm or fm.group(1) < oldest:
            continue
        if entry.st_mtime > settled_before:
            print(f"wait  {folder}/{name} (modified in the last {args.settle_minutes} min)")
            skipped += 1
            continue
        if in_bucket.get(name) == entry.st_size:
            continue
        print(f"copy  {folder}/{name} ({entry.st_size / 1e6:.0f} MB)")
        copied += 1
        if args.dry_run:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / name
            sftp.get(f"{SOURCE_ROOT}/{folder}/{name}", str(local))
            gcs.put_file(str(local), f"{dest}/{name}")

client.close()
print(f"{copied} file(s) {'to copy' if args.dry_run else 'copied'}, {skipped} still being written")
