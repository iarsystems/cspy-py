"""Example hardware-in-the-loop tests (here on the simulator) for examples/firmware.

Every test gets a fresh debug session stopped at main (the launch
configuration's stopOnSymbol). See examples/firmware/PUZZLE.md for what the
program does.
"""


def test_ring_slot_matches_iteration(cspy_session):
    """commit_history must store iteration n in slot n & 15."""
    cspy_session.client.breakpoints.add("commit_history")
    for _ in range(8):
        cspy_session.go_and_wait(timeout=10)
        assert cspy_session.eval_int("g_expected_slot") == cspy_session.eval_int("g_phase") & 15


def test_program_boots_and_finishes(cspy_session):
    # The program idles forever after printing DBG_DONE, so wait for that.
    terminal = cspy_session.client.terminal
    cspy_session.go()
    assert terminal.wait_for_text("DBG_DONE", timeout=30)
    assert terminal.text.startswith("DBG_BOOT")
