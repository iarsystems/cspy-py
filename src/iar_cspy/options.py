"""Project options through the OptionsService: the IDE options dialog's model.

OptionsService serves the category/option tree the IDE's options dialog
renders, as XML, and validates edits before they are committed. It is session
based::

    with client.options.open("/abs/path/app.ewp", "Debug") as opts:
        pages = opts.category_tree().xml          # <pages> with page ids
        page = opts.option_tree("General-GEN-TARGET")
        check = opts.update(page.id, updated=[{"optionDefinitionId": "...", "data": "..."}])
        if not check.verification_errors:
            opts.commit()                         # marks the project modified
    client.project.call("SaveEwpFile", {"filename": "/abs/path/app.ewp"})

Reading options can mutate the configuration (some target options persist
derived values when read), so treat a session as a transaction. For a flat
list of option ids and values without sessions or XML, use ProjectManager's
``GetOptionsForConfiguration``/``ApplyOptionsForConfiguration`` via
:meth:`Project.call` instead.

Like ProjectManager this is an IDE service, hosted by IarServiceLauncher or
iaride and not by CSpyServer2. The project has to be loaded in the project
manager first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ._thrift import to_plain
from .errors import CSpyError

if TYPE_CHECKING:
    from .client import Client


class ProjectNotLoaded(CSpyError):
    """The OptionsService does not know the project: load it with ``client.project.load``."""

    def __init__(self, message: str, project_path: str) -> None:
        super().__init__(message)
        self.project_path = project_path


def options_id(value: str, id_type: str = "OptionsService") -> dict[str, str]:
    """A ``shared.Id`` payload."""
    return {"value": str(value), "type": str(id_type)}


def check_response(response: Any, what: str) -> dict[str, Any]:
    """Convert an OptionsService response, raising on a non-success result.

    OptionsService responses carry a ``shared.Success {value, failureMessage}``
    rather than throwing, so failures are silent unless checked.
    """
    data = to_plain(response)
    success = data.get("success") if isinstance(data, dict) else None
    if isinstance(success, dict) and not success.get("value"):
        message = success.get("failureMessage") or "<no failure message>"
        raise CSpyError(f"OptionsService {what} failed: {message}")
    return data


@dataclass
class OptionTree:
    id: str | None
    xml: str | None


@dataclass
class OptionUpdate:
    tree_id: str
    xml: str | None
    verification_errors: list[Any] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.verification_errors


class Options:
    """Entry point for option sessions (``client.options``)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Any OptionsService RPC. The response's ``success`` field is not checked."""
        return self._client.call("options", method, *args, **kwargs)

    def open(
        self,
        project: str | None = None,
        configuration: str | None = None,
        *,
        node: str = "",
        show_hidden: bool = False,
    ) -> OptionsSession:
        """Open a session for a configuration (or, with ``node``, a file/group in it)."""
        ctx, cfg_name = self._client.project.resolve(project, configuration)
        request = {
            "projectPath": str(ctx.get("filename", "")),
            "configurationName": cfg_name,
            "nodePathOrIndex": str(node),
            "showHiddenOptions": bool(show_hidden),
        }
        try:
            data = check_response(self.call("CreateSession", request), "CreateSession")
        except CSpyError as exc:
            # OptionsService resolves the path through the project manager, so
            # the project has to be loaded there first.
            if "Project not found" in str(exc):
                raise ProjectNotLoaded(
                    f"{exc}. Load it into the project manager first with "
                    f'client.project.load("{request["projectPath"]}").',
                    request["projectPath"],
                ) from exc
            raise

        session = data.get("sessionId") if isinstance(data, dict) else None
        session_id = session.get("value") if isinstance(session, dict) else None
        return OptionsSession(
            self,
            str(session_id),
            project=ctx,
            configuration=cfg_name,
            read_only=data.get("readOnly") if isinstance(data, dict) else None,
            response=data,
        )

    def session(self, session_id: str) -> OptionsSession:
        """Re-attach to an existing session by id."""
        return OptionsSession(self, str(session_id))


class OptionsSession:
    """One OptionsService session; close it (or use ``with``) when done."""

    def __init__(
        self,
        options: Options,
        session_id: str,
        *,
        project: dict[str, Any] | None = None,
        configuration: str | None = None,
        read_only: bool | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        self._options = options
        self.id = session_id
        self.project = project
        self.configuration = configuration
        self.read_only = read_only
        self.response = response

    def __enter__(self) -> OptionsSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, **fields: Any) -> dict[str, Any]:
        return check_response(self._options.call(method, {"sessionId": options_id(self.id), **fields}), method)

    def category_tree(self) -> OptionTree:
        """The ``<pages>`` XML of categories and pages; page ids go to :meth:`option_tree`."""
        tree = _tree(self._request("GetCategoryTree"))
        return OptionTree(id=_tree_id(tree), xml=tree.get("data"))

    def option_tree(self, tree_id: str) -> OptionTree:
        """The option widgets and current values of one page, as XML."""
        tree = _tree(self._request("GetOptionTree", treeId=options_id(tree_id)))
        return OptionTree(id=_tree_id(tree) or tree_id, xml=tree.get("data"))

    def update(
        self,
        tree_id: str,
        updated: list[Any] | tuple[Any, ...] = (),
        created: list[Any] | tuple[Any, ...] = (),
        deleted: list[Any] | tuple[Any, ...] = (),
    ) -> OptionUpdate:
        """Apply ``OptionValue`` objects and have the backend verify them.

        Each value is ``{"optionDefinitionId": ..., "data": ...}`` (plus
        optional ``inherited`` and ``children``). Nothing is saved: call
        :meth:`commit` to keep an accepted state.
        """
        data = self._request(
            "UpdateOptionsState",
            treeId=options_id(tree_id),
            createdOptionValues=list(created),
            updatedOptionValues=list(updated),
            deletedOptionValues=list(deleted),
        )
        return OptionUpdate(
            tree_id=tree_id,
            xml=_tree(data).get("data"),
            verification_errors=list(data.get("verificationErrors") or []),
        )

    def commit(self) -> dict[str, Any]:
        """Write the verified state into the configuration (marks the project modified)."""
        return self._request("CommitOptionState")

    def close(self) -> dict[str, Any]:
        """Destroy the session, discarding uncommitted changes."""
        return self._request("DestroySession")


def _tree(data: Any) -> dict[str, Any]:
    tree = data.get("tree") if isinstance(data, dict) else None
    return tree if isinstance(tree, dict) else {}


def _tree_id(tree: dict[str, Any]) -> str | None:
    tree_id = tree.get("id")
    return tree_id.get("value") if isinstance(tree_id, dict) else None
