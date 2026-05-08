"""Launcher: starts all three Python services and fans their logs to stdout.

Usage:
    python run.py                 # start weather + billing + orchestrator
    python run.py weather         # start a subset
    python run.py weather billing

Ctrl+C terminates all children cleanly.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Force pure-Python protobuf so shared.proto_compat can monkey-patch
# FieldDescriptor.label (gone in protobuf 5.x+, used by older a2a-sdk).
# Subprocesses inherit this env var via os.environ.copy() in _spawn.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

# ANSI colors per service, cycling. Windows 10+ terminals support these.
_COLORS = ["\033[36m", "\033[33m", "\033[35m", "\033[32m", "\033[34m"]
_RESET = "\033[0m"


@dataclass
class Service:
    name: str
    module: str           # python -m <module>
    port_env: str         # env var that holds the port (for log prefix only)
    default_port: int


SERVICES: dict[str, Service] = {
    "weather": Service("weather", "agents.weather_agent.main", "WEATHER_AGENT_PORT", 9101),
    "billing": Service("billing", "agents.billing_agent.main", "BILLING_AGENT_PORT", 9102),
    "orchestrator": Service("orchestrator", "orchestrator.main", "ORCHESTRATOR_PORT", 3000),
}


def _pipe(stream, prefix: str, color: str) -> None:
    for line in iter(stream.readline, b""):
        try:
            text = line.decode(errors="replace").rstrip()
        except Exception:
            continue
        sys.stdout.write(f"{color}{prefix}{_RESET} {text}\n")
        sys.stdout.flush()
    stream.close()


def _spawn(svc: Service, color: str) -> subprocess.Popen:
    port = os.environ.get(svc.port_env, str(svc.default_port))
    prefix = f"[{svc.name:>12} :{port}]"
    print(f"{color}{prefix}{_RESET} starting `python -m {svc.module}`")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", svc.module],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    threading.Thread(
        target=_pipe, args=(proc.stdout, prefix, color), daemon=True
    ).start()
    return proc


def main() -> int:
    requested = sys.argv[1:] or list(SERVICES.keys())
    unknown = [r for r in requested if r not in SERVICES]
    if unknown:
        print(f"unknown service(s): {unknown}. known: {list(SERVICES)}", file=sys.stderr)
        return 2

    procs: list[tuple[Service, subprocess.Popen]] = []
    for i, name in enumerate(requested):
        svc = SERVICES[name]
        color = _COLORS[i % len(_COLORS)]
        procs.append((svc, _spawn(svc, color)))

    def shutdown(*_):
        print("\nshutting down…")
        for svc, p in procs:
            if p.poll() is None:
                try:
                    if os.name == "nt":
                        p.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    else:
                        p.terminate()
                except Exception:
                    pass
        for svc, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    exit_code = 0
    try:
        while procs:
            for svc, p in list(procs):
                rc = p.poll()
                if rc is not None:
                    print(f"[{svc.name}] exited with code {rc}")
                    procs.remove((svc, p))
                    exit_code = rc or exit_code
            if not procs:
                break
            # block on any child — poll loop with small sleep is fine here.
            try:
                procs[0][1].wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        shutdown()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
