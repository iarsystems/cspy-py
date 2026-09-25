"""End-to-end demo of the iar_cspy API on the bundled simulator firmware.

Starts a managed backend, runs the program to main, sets a breakpoint, runs
to it, inspects state, then runs the program to completion and prints what it
wrote to its console.

    python examples/scripts/live_demo.py --iar-path /opt/iar/ewarm
    python examples/scripts/live_demo.py --cspyserver2 /path/to/CSpyServer2

Without arguments the IAR_INSTALL_PATH / THRIFT_CSPYSERVER_EXE environment
variables are used.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import iar_cspy as cspy

LAUNCH = Path(__file__).resolve().parents[1] / "firmware" / "launch.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iar-path", help="IAR installation (the directory with common/bin)")
    parser.add_argument("--cspyserver2", help="CSpyServer2 executable, instead of --iar-path")
    args = parser.parse_args()

    overrides = {"cspy_executable": args.cspyserver2} if args.cspyserver2 else {}

    with cspy.launch(args.iar_path, **overrides) as client:  # the rest from the environment
        print("C-SPY", client.debugger.version())

        with client.debugger.session(LAUNCH) as dbg:  # runs to stopOnSymbol ("main")
            print("stopped at main:", dbg.core_state().name)

            bp = client.breakpoints.add("commit_history")
            dbg.go_and_wait(timeout=10)
            print(f"hit breakpoint {bp.id} on {bp.ule}; g_phase = {dbg.eval_int('g_phase')}")

            regs = dbg.registers(limit=4)
            print("registers:", ", ".join(f"{r.name}=0x{r.value:08x}" for r in regs.registers))

            raw = client.memory.read(dbg.eval("g_guard"), 4)  # the variable's own memory
            print("g_guard in memory:", raw[::-1].hex())

            client.breakpoints.remove(bp)
            dbg.go()
            client.terminal.wait_for_text("DBG_DONE", timeout=30)  # then it idles forever
            dbg.halt()
            print("program output:")
            print(client.terminal.text.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
