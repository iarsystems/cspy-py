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
