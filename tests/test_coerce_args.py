from __future__ import annotations

import pytest
from thriftpy2.protocol import TBinaryProtocol
from thriftpy2.transport import TMemoryBuffer

from iar_cspy._thrift import BUNDLED_IDL_DIR as THRIFT_DIR
from iar_cspy._thrift import coerce_call_args, load_thrift_module
from iar_cspy.errors import CSpyError


@pytest.fixture(scope="module")
def cspy_module():
    return load_thrift_module(str(THRIFT_DIR / "cspy.thrift"), (str(THRIFT_DIR),))


@pytest.fixture(scope="module")
def shared_module():
    # Loaded directly (not via cspy.thrift's include), so its struct classes
    # are distinct class objects from the ones in cspy_module's specs. This
    # mirrors how Idl.shared behaves in the client.
    return load_thrift_module(str(THRIFT_DIR / "shared.thrift"), (str(THRIFT_DIR),))


def encode_args(service_cls, method: str, args: tuple) -> bytes:
    """Binary-encode a call's args struct, i.e. what thriftpy2 does on the wire."""
    args_cls = getattr(service_cls, f"{method}_args")
    inst = args_cls()
    for fid in sorted(args_cls.thrift_spec):
        idx = fid - 1
        if idx < len(args):
            setattr(inst, args_cls.thrift_spec[fid][1], args[idx])
    buf = TMemoryBuffer()
    inst.write(TBinaryProtocol(buf))
    return buf.getvalue()


def test_dict_becomes_context_ref_and_encodes(cspy_module):
    args, kwargs = coerce_call_args(
        cspy_module.Debugger,
        "evalExpression",
        ({"type": 1, "level": 0, "core": 0, "task": 0}, "argc", [], 0, False),
        {},
    )
    assert type(args[0]).__name__ == "ContextRef"
    assert args[0].type == 1
    assert kwargs == {}
    assert encode_args(cspy_module.Debugger, "evalExpression", args)


def test_enum_names_accepted_with_and_without_k_prefix(cspy_module, shared_module):
    args, _ = coerce_call_args(
        cspy_module.Debugger,
        "evalExpression",
        ({"type": "CurrentInspection"}, "argc", [], "kDefault", False),
        {},
    )
    assert args[0].type == shared_module.ContextType.CurrentInspection
    assert args[3] == shared_module.ExprFormat.kDefault

    args, _ = coerce_call_args(
        cspy_module.Debugger,
        "evalExpression",
        ({"type": "Stack"}, "x", [], "Default", False),
        {},
    )
    assert args[3] == shared_module.ExprFormat.kDefault


def test_struct_instance_from_other_module_passes_through(cspy_module, shared_module):
    # The same IDL struct loaded twice yields distinct classes; coercion must
    # pass such instances through untouched (they encode fine).
    ref = shared_module.ContextRef(
        type=shared_module.ContextType.CurrentBase, level=0, core=0, task=0
    )
    args, _ = coerce_call_args(
        cspy_module.Debugger,
        "evalExpression",
        (ref, "n", [], shared_module.ExprFormat.kDefault, False),
        {},
    )
    assert args[0] is ref
    assert encode_args(cspy_module.Debugger, "evalExpression", args)


def test_scalar_for_struct_field_raises_actionable_error(cspy_module):
    with pytest.raises(CSpyError, match="expects a ContextRef struct"):
        coerce_call_args(cspy_module.Debugger, "evalExpression", ("argc",), {})


def test_unknown_struct_field_raises_with_valid_fields(cspy_module):
    with pytest.raises(CSpyError, match=r"Unknown field\(s\) typ .*core, level, task, type"):
        coerce_call_args(
            cspy_module.Debugger,
            "evalExpression",
            ({"typ": 1}, "argc", [], 0, False),
            {},
        )


def test_unknown_enum_name_raises_with_valid_names(cspy_module):
    with pytest.raises(CSpyError, match="Unknown ContextType name 'Bogus'"):
        coerce_call_args(
            cspy_module.Debugger,
            "evalExpression",
            ({"type": "Bogus"}, "argc", [], 0, False),
            {},
        )


def test_kwargs_are_coerced(cspy_module):
    _, kwargs = coerce_call_args(
        cspy_module.Debugger,
        "evalExpression",
        (),
        {"ref": {"type": 0}, "expr": "argc"},
    )
    assert type(kwargs["ref"]).__name__ == "ContextRef"
    assert kwargs["expr"] == "argc"


def test_list_of_dicts_becomes_list_of_structs(cspy_module):
    spec = cspy_module.Debugger.goToLocations_args.thrift_spec
    fid = min(fid for fid, fs in spec.items() if fs[0] == 15)  # the list field
    struct_cls = spec[fid][2][1]
    payload = {fs[1]: None for fs in struct_cls.thrift_spec.values()}

    positions = sorted(spec)
    args_in: list = [None] * len(positions)
    args_in[positions.index(fid)] = [payload]

    args, _ = coerce_call_args(cspy_module.Debugger, "goToLocations", tuple(args_in), {})
    out = args[positions.index(fid)]
    assert type(out[0]).__name__ == struct_cls.__name__


def test_unknown_method_passes_args_through(cspy_module):
    args, kwargs = coerce_call_args(cspy_module.Debugger, "noSuchMethod", ({"a": 1},), {"b": 2})
    assert args == ({"a": 1},)
    assert kwargs == {"b": 2}
