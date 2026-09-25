---
name: iar-cspy
description: Debug or test embedded firmware with the IAR C-SPY debugger (simulator or hardware) by writing Python scripts against the iar_cspy API. Use when asked to run, step, break on, inspect (registers, memory, variables, program output) or test a program under C-SPY, or to build/debug an IAR Embedded Workbench project, especially for multi-step work where one script beats many tool calls.
---

# Driving C-SPY with the iar_cspy Python API

`iar_cspy` scripts the IAR C-SPY debugger: start a session, set breakpoints,
run, and read registers, memory, variables and program output. Write a short
script and run it, rather than issuing many single debugger commands.

## Find the documentation

Read the documentation before writing code. Do not guess method names.

1. **Installed package** (always matches the installed version):
   `python -m pydoc iar_cspy` for the overview, then
   `python -m pydoc iar_cspy.debugger.Debugger` (or `.breakpoints`,
   `.memory`, `.terminal`, `.project`, ...) for details. The source is next to
   it: `python -c "import iar_cspy, os; print(os.path.dirname(iar_cspy.__file__))"`.
2. **Guides** in the cspy-py repository (<https://github.com/iarsystems/cspy-py>):
   - `docs/python-api.md`: the model and recipes. Start here.
   - `README.md`: the objects at a glance, errors, testing.
   - `docs/backend-notes.md`: backend behavior. Read it when a call fails
     unexpectedly.
   - `examples/scripts/`, `examples/pytest/`: complete working examples.

## Setup

- Check the package: `python -c "import iar_cspy"`. If that fails, install it
  from a checkout of cspy-py (`pip install <checkout>`). Ask
  the user where the checkout is instead of cloning it yourself.
- You need a backend. That is either an IAR installation path (the directory
  with `common/bin`; `IAR_INSTALL_PATH` may already be set) or the service
  registry port of a running IDE or backend. If neither is known, ask the user.
  You also need a launch configuration (a `launch.json` from the project, or
  an Embedded Workbench `.ewp`/`.eww`).

## Working rules

- Put a timeout on every wait, and make sure the script shuts the session and
  backend down on every path. Use the context managers the docs show.
- Some calls (call stack, source lookup, disassembly) need an IDE frontend
  and fail when running headless. `docs/backend-notes.md` explains why and
  what to use instead.
- Report findings as values read from the target, together with the script
  you ran, so the user can rerun it.
- For firmware tests, use the pytest plugin as described in the docs, rather
  than hand-rolled setup and teardown.
