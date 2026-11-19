# Open items

Items that concern the package or the backend behavior it wraps. They come from
the roadmap of the MCP server (https://github.com/iarsystems/cspy-mcp), which
tracks the tool-surface items; the numbers are kept from there.

## Status Legend
- [ ] Not started
- [~] In progress
- [x] Done

14. [~] Breakpoint tools are effectively sim-only against emulator drivers
- The package's `add_source`/`add_code`/`add_data`/`add_log` set breakpoints
  from descriptors with the driver's category, as the VS Code extension
  does. Open: verify on emulator drivers, and use them in the MCP tools.
- Root cause is two known backend bugs in breakpoint category handling
  (default categories only cover STD_CODE*/STD_DATA*; explicit category ids
  outside the backend translation map are silently dropped, so even the
  correct `EMUL_CODE` fails). Backend fixes are tracked separately, but until
  they land:
- The breakpoint tool errors should mention the working alternatives:
  `debugger_call runToULE ["<ule>"]`, or eval `__setCodeBreak("main", 0, "1",
  "TRUE", "")`. Consider first-class `debugger_run_to_ule` and a macro-based
  breakpoint fallback tool.

15. [~] Clarify the access_type enum
- Docstring says 1 = execute/fetch, but created breakpoints report
  `accessType: 0` and the sim accepts both 0 and 1.
- Verified: `shared.AccessType` is 1-4 (1 = kDkFetchAccess), and a fetch
  breakpoint on the Cortex-M3 simulator reads back `accessType=0`. The docs
  now say to pass 1-4 and not to rely on the value read back
  (docs/backend-notes.md). Open: whether 0 is a backend bug, and what emulator
  drivers report.

16. [ ] Fix cycle counter signedness
- `debugger_get_cycle_counter` returned -821365371 on hardware (i32 truncation
  of the 64-bit CYCLECOUNTER). Return unsigned 64-bit.

17. [~] Surface the known stopSession backend crash better
- Every `stopSession` in `-standalone -sockets` mode hits a known backend
  assertion and aborts the process; managed mode recovers but the error
  payload embeds the same ~30-line stack trace twice (~30 KB).
  Deduplicate/truncate, and tag it as a known backend issue with
  "retry configure_and_start once" as the recovery hint.
- Managed mode no longer calls `stopSession`: it ends the session with
  `Debugger.exit()`, as cspy.thrift prescribes and the VS Code extension
  does. Open: the standalone-mode path (a process we must not exit), and
  whether the assertion still occurs (not reproduced with EWARM 10.10).

21. [ ] Host a headless `frontend` service
- Without an IDE, `getStack`, `getContextInfo`, source lookup and disassembly
  at the PC fail with "No such service, serviceName=frontend": the backend calls
  back into the IDE's UI service (`frontend.thrift`: message boxes, file
  dialogs). A stand-in hosted like the event handler would likely unblock them,
  but it has to answer the backend's prompts on the user's behalf, so decide
  on a policy first (e.g. default answer + log every prompt).

23. [ ] Fix register reads (`client.debugger.registers()`)
- The CSpyMemory interface has historically been very picky about reading SFR
  and other non-regular memory spaces. This has struck e.g. reading multiple
  registers. After the fixes in version 1.1 of the interface (MR !2411,
  IDE-8591-BB-remote-read-sfr-units) this has been fixed. One can now read a
  whole block of registers in one call.
- Verified 2026-11-19 on an Arm (Cortex-M) target, with the !2411 changes: the CPU registers are zone 4,
  one 32-bit unit per address (R0..R12 = 0..12, R14 = 14, PC = 15, ... 0..29,
  contiguous). One `readMemory(zone4@0, wordsize=4, bitsize=32, count=30)`
  returns the same 29 values as 29 separate `count=1` reads (0 mismatches).
  `registers()` still reads each register as `readMemory(loc, wordsize=1,
  bitsize=8, count=bytes)`, i.e. four 8-bit units starting at the register's
  address, which spans the neighbouring registers' addresses. A burst of those
  preceded an iaride crash (reading many registers at once); not confirmed as
  the cause.
- Fix: read each register as `wordsize=bit_size/8, bitsize=bit_size, count=1`,
  and read contiguous register addresses of a zone as one block instead of one
  call per register. Give `registers()` a default `limit` while at it.
- Backwards compatibility: this is a silent improvement in the backend's Thrift
  implementation (nothing in the IDL changed), so older backends may still
  expect, or only work with, the byte-unit read. Select the read style by the
  backend's API version rather than guessing from errors. `CSpyMemory` has
  `GetServiceVersion()` (memory.thrift); the block read needs version 1.1 or
  later. Nothing in `iar_cspy` uses it yet, and the vendored `memory.thrift`
  still says 1.0 (`CSPYMEMORY_VERSION`), so refresh it from `ew/ew` too. Keep
  the old byte-unit path for older versions.
