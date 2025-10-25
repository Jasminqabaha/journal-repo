#!/usr/bin/env python3
import argparse, os, sys, time, json, socket
import multiprocessing as mp
from datetime import datetime
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import stat as pystat
import requests

FIFO_PATH = "/tmp/djs_queue"     
LOG_DIRNAME = "logs"
LOG_FILE = "app.log"
ENTRIES_DIRNAME = "entries"

def setup_logging(repo_dir: Path):
    log_dir = repo_dir / LOG_DIRNAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s => %(message)s")
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    fh = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)


def fetch_weather(lat: float, lon: float):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {"latitude": lat, "longitude": lon, "current_weather": "true"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        cw = r.json().get("current_weather") or {}
        return {
            "temperature": cw.get("temperature"),
            "windspeed": cw.get("windspeed"),
            "weathercode": cw.get("weathercode"),
            "time": cw.get("time"),
        }
    except Exception as e:
        logging.getLogger("weather").warning(f"weather fetch failed: {e}")
        return None
    
def ensure_fifo(path: str):
    if os.path.exists(path):
        st = os.stat(path)
        if not pystat.S_ISFIFO(st.st_mode):
            os.remove(path)
            os.mkfifo(path, 0o666)
    else:
        os.mkfifo(path, 0o666)

def md_path_for_today(repo_dir: Path) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    entries = repo_dir / ENTRIES_DIRNAME
    entries.mkdir(parents=True, exist_ok=True)
    return entries / f"{today}.md"

def write_header_if_new(md_path: Path, with_weather: bool, lat: float, lon: float):
    if md_path.exists() and md_path.stat().st_size > 0: 
        return
    lines = [f"# {md_path.stem}\n"]
    if with_weather:
        w = fetch_weather(lat, lon)
        if w and w.get("temperature") is not None:
            lines.append(f"*Weather:* {w['temperature']}°C, wind {w.get('windspeed','?')} km/h, at {w.get('time','now')}\n")
    lines.append("\n---\n\n")
    md_path.write_text("".join(lines), encoding="utf-8")

def append_entry(md_path: Path, message: str):
    ts = datetime.now().strftime("%H:%M")
    with open(md_path, "a", encoding="utf-8") as f:
        f.write(f"- {ts} {message}\n")

def fork_and_push(repo_dir: Path, md_path: Path):
    import os
    script = repo_dir / "push.sh"
    if not script.exists():
        logging.error(f"push.sh not found at {script}")
        return
    pid = os.fork()
    if pid == 0:
        try:
            os.execv("/bin/bash", ["/bin/bash", str(script), str(repo_dir), str(md_path)])
        except Exception as e:
            print(f"[child] exec failed: {e}", file=sys.stderr, flush=True)
            os._exit(127)

def writer_loop(repo_dir: Path, with_weather: bool, lat: float, lon: float, q: mp.Queue):
    log = logging.getLogger("writer")
    md_path = md_path_for_today(repo_dir)
    write_header_if_new(md_path, with_weather, lat, lon)
    log.info(f"writer started. writing to {md_path}")
    SIZE_LIMIT = 10_240  # 10KB
    while True:
        try:
            msg = q.get()
            if not isinstance(msg, str): 
                continue
            m = msg.strip()
            if not m: 
                continue
            today_path = md_path_for_today(repo_dir)
            if today_path != md_path:
                md_path = today_path
                write_header_if_new(md_path, with_weather, lat, lon)
                log.info(f"new day -> switching to {md_path}")

            if not md_path.exists():
                write_header_if_new(md_path, with_weather, lat, lon)
            append_entry(md_path, m)
            log.info(f"appended entry: {m!r}")
            if md_path.stat().st_size >= SIZE_LIMIT:
                log.info(f"{md_path.name} reached >=10KB; pushing to GitHub...")
                fork_and_push(repo_dir, md_path)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception(f"writer error: {e}")
            

def start_daemon(args):
    repo_dir = Path(args.repo).expanduser().resolve() 
    if not repo_dir.exists():
        print(f"repo path not found: {repo_dir}", file=sys.stderr)
        sys.exit(1)
    setup_logging(repo_dir)
    log = logging.getLogger("main")
    log.info("starting Daily-journal-sync...")
    q = mp.Queue()
    p = mp.Process(target=writer_loop, args=(repo_dir, args.with_weather, args.lat, args.lon, q), daemon=True)
    p.start()
    log.info(f"writer pid={p.pid}")
    ensure_fifo(FIFO_PATH)
    log.info(f"listening on FIFO {FIFO_PATH}")
    while True:
        try:
            with open(FIFO_PATH, "r", buffering=1, encoding="utf-8") as fifo:
                for line in fifo: 
                    q.put(line.strip())
        except KeyboardInterrupt:
            log.info("shutting down by KeyboardInterrupt")
            break
        except Exception as e:
            log.exception(f"fifo read error: {e}")
            time.sleep(1)
    p.join(timeout=2)

def add_note(args):
    if not os.path.exists(FIFO_PATH):
        print("Error: service not running (FIFO missing). Start with: python3 main.py start --repo ~/journal-repo", file=sys.stderr)
        sys.exit(2)
    with open(FIFO_PATH, "w", encoding="utf-8") as fifo:
        fifo.write(args.message.strip() + "\n")
    print("Added.")

def build_parser():
    p = argparse.ArgumentParser(
        prog="daily-journal-sync", 
        description="Tiny queue-based journaling with weather + GitHub push"
        )
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("start", help="start the background service")
    sp.add_argument("--repo", required=True, help="path to your journal repo (will contain entries/ and logs/)")
    sp.add_argument("--with-weather", action="store_true", help="include current weather in daily header")
    sp.add_argument("--lat", type=float, default=31.5326, help="latitude for weather")
    sp.add_argument("--lon", type=float, default=35.0998, help="longitude for weather")
    sp.set_defaults(func=start_daemon)
    ap = sub.add_parser("add", help="add a journal note via the running service")
    ap.add_argument("message", help="note text to append")
    ap.set_defaults(func=add_note)
    return p

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)

if __name__ == "__main__":
    main()
