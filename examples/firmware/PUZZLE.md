# Debug Puzzle: Phantom Slot Shift

Target program:
- `tests/live_assets/main.c`
- built output: `tests/live_assets/Debug/Exe/test.out`

## Story
A ring buffer write path is expected to store each iteration result at `iter & 15`.
Sometimes, one iteration stores into a different slot.

The bug is deterministic but data-dependent.

## Puzzle Signals
Watch these globals while running:
- `g_phase`: current iteration
- `g_expected_slot`: expected ring slot (`iter & 15`)
- `g_observed_slot`: slot actually written
- `g_mismatch_iter`: first iter where observed != expected (`0xFFFFFFFF` means none yet)
- `g_guard`: flips with a signature when mismatch is first detected

## Suggested MCP Walkthrough
1. Configure/start session using `tests/live_assets/launch.json`.
2. Set code breakpoint on `main`.
3. Set code breakpoint on source ULE around `commit_history` call site.
4. Run and inspect:
   - `debugger_register_snapshot`
   - `libsupport_get_output`
5. Continue until `g_mismatch_iter` changes from `0xFFFFFFFF`.
6. Investigate control/data path into `commit_history`.

## Hint
The slot anomaly is tied to both iteration index and the top bit of computed state.
