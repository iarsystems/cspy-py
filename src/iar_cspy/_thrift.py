"""Thrift plumbing: IDL loading, argument coercion, result conversion."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

import thriftpy2
from thriftpy2.thrift import TType

from .errors import CSpyError

#: The IDL files bundled with this package.
BUNDLED_IDL_DIR = Path(__file__).resolve().parent / "idl"


def _module_name_for(path: Path) -> str:
    digest = hashlib.md5(str(path).encode("utf-8")).hexdigest()
    return f"cspy_{digest}_thrift"


@lru_cache(maxsize=32)
def load_thrift_module(thrift_file: str, include_dirs: tuple[str, ...]) -> Any:
    path = Path(thrift_file)
    if not path.exists():
        raise CSpyError(f"Thrift file not found: {path}")

    try:
        return thriftpy2.load(
            str(path),
            module_name=_module_name_for(path),
            include_dirs=list(include_dirs),
        )
    except Exception as exc:  # noqa: BLE001
        raise CSpyError(
            f"Failed to load thrift IDL {path}. Check THRIFT_FILE and THRIFT_INCLUDE_DIRS "
            "(for cspy.thrift, shared.thrift must be resolvable)."
        ) from exc


class Idl:
    """Loads the IDL modules for one (thrift_file, include_dirs) pair."""

    def __init__(self, thrift_file: Path, include_dirs: list[str] | tuple[str, ...]) -> None:
        self.thrift_file = Path(thrift_file)
        self.include_dirs = tuple(str(d) for d in include_dirs)

    def find(self, filename: str) -> Path:
        for include_dir in self.include_dirs:
            candidate = Path(include_dir) / filename
            if candidate.exists():
                return candidate
        raise CSpyError(f"Required thrift file not found in THRIFT_INCLUDE_DIRS: {filename}")

    def module(self, filename: str | None = None) -> Any:
        """The module for ``filename``, or for the main cspy.thrift when None."""
        path = self.thrift_file if filename is None else self.find(filename)
        return load_thrift_module(str(path), self.include_dirs)

    def service(self, filename: str | None, name: str) -> Any:
        service = getattr(self.module(filename), name, None)
        if service is None:
            where = filename or self.thrift_file.name
            raise CSpyError(f"Service '{name}' not found in {where}")
        return service

    @property
    def shared(self) -> Any:
        return self.module("shared.thrift")

    @property
    def registry(self) -> Any:
        try:
            return self.module("ServiceRegistry.thrift")
        except CSpyError as exc:
            raise CSpyError(
                "ServiceRegistry.thrift not found in THRIFT_INCLUDE_DIRS; cannot resolve services via registry"
            ) from exc


def method_names(service_cls: Any) -> list[str]:
    methods = getattr(service_cls, "thrift_services", None)
    if isinstance(methods, list):
        return sorted(str(item) for item in methods)
    if isinstance(methods, dict):
        return sorted(methods.keys())
    return sorted(
        name for name in dir(service_cls) if not name.startswith("_") and callable(getattr(service_cls, name, None))
    )


def to_plain(value: Any) -> Any:
    """Convert thrift objects into JSON-serializable plain Python structures.

    Structs become dicts keyed by field name, bytes become hex strings.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        return [to_plain(v) for v in value]
    if isinstance(value, tuple):
        return [to_plain(v) for v in value]
    if isinstance(value, set):
        return [to_plain(v) for v in sorted(value, key=repr)]
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}

    thrift_spec = getattr(value, "thrift_spec", None)
    if thrift_spec is not None:
        payload = {}
        items = thrift_spec.values() if isinstance(thrift_spec, dict) else thrift_spec
        for item in items:
            if not item:
                continue
            name = None
            if len(item) >= 2 and isinstance(item[1], str):
                name = item[1]
            elif len(item) >= 3 and isinstance(item[2], str):
                name = item[2]
            if not name:
                continue
            payload[name] = to_plain(getattr(value, name, None))
        return payload

    if hasattr(value, "__dict__"):
        return {k: to_plain(v) for k, v in vars(value).items() if not k.startswith("_")}

    return repr(value)


def _split_container_spec(spec: Any) -> tuple[int, Any]:
    # Container element/key/value specs are either a bare TType int or a
    # (ttype, nested_info) tuple for structs and nested containers.
    if isinstance(spec, tuple):
        return spec[0], spec[1]
    return spec, None


def _enum_value_from_name(enum_cls: Any, name: str, field_name: str) -> int:
    for candidate in (name, f"k{name}"):
        value = getattr(enum_cls, candidate, None)
        if isinstance(value, int):
            return value
    names = sorted(getattr(enum_cls, "_NAMES_TO_VALUES", {}))
    raise CSpyError(
        f"Unknown {enum_cls.__name__} name {name!r} for field '{field_name}'."
        + (f" Valid names: {', '.join(names)}" if names else "")
    )


def _coerce_value(value: Any, ttype: int, type_info: Any, field_name: str) -> Any:
    """Coerce a JSON-shaped value into what thriftpy2's encoder expects."""
    if value is None:
        return None

    if ttype == TType.STRUCT:
        # Already a struct instance: pass through. Checked via thrift_spec, not
        # isinstance, because the same IDL struct may be loaded as distinct
        # classes (e.g. shared.thrift loaded directly and via cspy.thrift).
        if hasattr(value, "thrift_spec"):
            return value
        if not isinstance(value, dict):
            field_names = [spec[1] for spec in type_info.thrift_spec.values()]
            raise CSpyError(
                f"Field '{field_name}' expects a {type_info.__name__} struct; pass a JSON "
                f"object with fields: {', '.join(field_names)} (got {type(value).__name__})"
            )
        known = {spec[1]: spec for spec in type_info.thrift_spec.values()}
        unknown = sorted(set(value) - set(known))
        if unknown:
            raise CSpyError(
                f"Unknown field(s) {', '.join(unknown)} for struct {type_info.__name__} "
                f"in '{field_name}'. Valid fields: {', '.join(sorted(known))}"
            )
        inst = type_info()
        for name, spec in known.items():
            if name in value:
                inner_ttype = spec[0]
                inner_info = spec[2] if len(spec) == 4 else None
                setattr(inst, name, _coerce_value(value[name], inner_ttype, inner_info, name))
        return inst

    if ttype == TType.I32 and type_info is not None and isinstance(value, str):
        return _enum_value_from_name(type_info, value, field_name)

    if ttype in (TType.LIST, TType.SET) and isinstance(value, (list, tuple)):
        elem_ttype, elem_info = _split_container_spec(type_info)
        return [_coerce_value(v, elem_ttype, elem_info, field_name) for v in value]

    if ttype == TType.MAP and isinstance(value, dict):
        key_spec, val_spec = type_info
        key_ttype, key_info = _split_container_spec(key_spec)
        val_ttype, val_info = _split_container_spec(val_spec)
        return {
            _coerce_value(k, key_ttype, key_info, field_name):
                _coerce_value(v, val_ttype, val_info, field_name)
            for k, v in value.items()
        }

    return value


def coerce_call_args(
    service_cls: Any, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Coerce JSON-shaped call arguments to the method's thrift arg types.

    Dicts become struct instances (recursively), enum names become their int
    values. Values already of the right type pass through unchanged, so this
    is a no-op for callers that construct thrift structs themselves.
    """
    args_cls = getattr(service_cls, f"{method}_args", None)
    spec = getattr(args_cls, "thrift_spec", None)
    if not spec:
        return args, kwargs

    field_specs = [spec[fid] for fid in sorted(spec)]

    def coerce_field(field_spec: Any, value: Any) -> Any:
        ttype = field_spec[0]
        type_info = field_spec[2] if len(field_spec) == 4 else None
        return _coerce_value(value, ttype, type_info, field_spec[1])

    new_args = tuple(
        coerce_field(field_specs[i], v) if i < len(field_specs) else v
        for i, v in enumerate(args)
    )
    by_name = {field_spec[1]: field_spec for field_spec in field_specs}
    new_kwargs = {
        k: coerce_field(by_name[k], v) if k in by_name else v for k, v in kwargs.items()
    }
    return new_args, new_kwargs
