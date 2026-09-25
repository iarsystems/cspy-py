"""Stress the session lifecycle: repeated configure/start/stop cycles.

Useful for reproducing backend crashes and connection resets around session
start and stop. Every cycle uses the bundled simulator firmware.

    python examples/scripts/repro_session_cycles.py --iar-path /opt/iar/ewarm --loops 40
"""

from __future__ import annotations

import argparse
import time
import traceback
from pathlib import Path

import iar_cspy as cspy

LAUNCH = Path(__file__).resolve().parents[1] / "firmware" / "launch.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iar-path", help="IAR installation (the directory with common/bin)")
    parser.add_argument("--cspyserver2", help="CSpyServer2 executable, instead of --iar-path")
    parser.add_argument("--loops", type=int, default=40, help="Number of cycles to run")
    parser.add_argument("--stop-on-failure", action="store_true", help="Stop at the first failure")
    parser.add_argument("--sleep-s", type=float, default=0.05, help="Delay between cycles in seconds")
    parser.add_argument(
        "--raw-stop",
        action="store_true",
        help="Stop with a bare stopSession RPC instead of stop_session() (skips the managed restart)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    overrides = {}
    if args.iar_path:
        overrides["iar_path"] = args.iar_path
    if args.cspyserver2:
        overrides["cspy_executable"] = args.cspyserver2
    config = cspy.Config.from_env(**overrides)
    loops = max(1, args.loops)
    failures = 0

    print(f"[repro] {loops} configure/start/stop cycles, {config.mode} backend")
    with cspy.Client(config) as client:
        for i in range(1, loops + 1):
            try:
                result = client.debugger.configure_and_start(LAUNCH)
                print(f"[repro] cycle {i} started, ran to {result.stop_on_symbol}: {result.ran_to_symbol}")
                if args.raw_stop:
                    client.debugger.call("stopSession")
                else:
                    client.debugger.stop_session()
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"[repro] cycle {i} FAILURE: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                print(f"[repro] classified: {client.classify_error(exc)['code']}")
                print(f"[repro] session: {client.debugger.state}")
                print(f"[repro] backend: {client.backend.status()}")
                if args.stop_on_failure:
                    break
            time.sleep(max(0.0, args.sleep_s))

    print(f"[repro] done. failures={failures}/{loops}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
