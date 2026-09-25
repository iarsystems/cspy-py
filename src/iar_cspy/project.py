"""Embedded Workbench projects through the ProjectManager service.

ProjectManager is an IDE service: it is hosted by IarServiceLauncher or a
Thrift-enabled iaride, not by CSpyServer2 (see :mod:`iar_cspy.ide_services`).
A managed backend started from a full IAR installation hosts it.

The edit/build/debug loop::

    client.project.load("/abs/path/app.eww")
    result = client.project.build()
    if not result.succeeded:
        print("\\n".join(result.tail(50)))
    client.project.start_debugging(build=False)

Arguments named ``project`` take a ``.ewp`` path and default to the current
project; ``configuration`` defaults to that project's current configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._thrift import to_plain
from .errors import CSpyError
from .types import BuildResult, ProjectDebugResult, ProjectInfo, ProjectStatus

if TYPE_CHECKING:
    from .client import Client

#: ``FileCollectionType`` names accepted by :meth:`Project.files`.
FILE_COLLECTIONS = (
    "ProjFiles",
    "ProjAndUserIncludeFiles",
    "ProjAndAllIncludeFiles",
    "WsFiles",
    "WsAndUserIncludeFiles",
    "WsAndAllIncludeFiles",
)


class Project:
    """Workspaces, builds and launch configurations (``client.project``)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Any ProjectManager RPC, e.g. ``call("SaveEwpFile", {"filename": path})``."""
        return self._client.call("projectmanager", method, *args, **kwargs)

    def load(self, path: str, fetch_dependency_data: bool = True) -> ProjectStatus:
        """Load a workspace (.eww, with its projects) or a single project (.ewp).

        Returns :meth:`status`.
        """
        path = str(path)
        method = "LoadEwpFile" if path.lower().endswith(".ewp") else "LoadEwwFile"
        self.call(method, path, bool(fetch_dependency_data))
        return self.status()

    def status(self) -> ProjectStatus:
        """Loaded projects with their configurations, and the current selection."""
        has_workspace = bool(self.call("HasWorkspace"))
        projects = to_plain(self.call("GetProjects")) or []

        try:
            current_project = to_plain(self.call("GetCurrentProject"))
        except Exception:  # noqa: BLE001 - no current project is a normal state
            current_project = None

        out_projects: list[ProjectInfo] = []
        for prj in projects:
            entry = ProjectInfo(project=prj)
            try:
                entry.configurations = to_plain(self.call("GetConfigurations", prj))
            except Exception as exc:  # noqa: BLE001
                entry.errors["configurations"] = str(exc)
            try:
                entry.current_configuration = to_plain(self.call("GetCurrentConfiguration", prj))
            except Exception as exc:  # noqa: BLE001
                entry.errors["current_configuration"] = str(exc)
            out_projects.append(entry)

        return ProjectStatus(has_workspace=has_workspace, current_project=current_project, projects=out_projects)

    def resolve(self, project: str | None = None, configuration: str | None = None) -> tuple[dict[str, Any], str]:
        """(ProjectContext dict, configuration name), defaulting to the current ones."""
        if project:
            ctx: dict[str, Any] = {"filename": str(project)}
        else:
            ctx = to_plain(self.call("GetCurrentProject"))
            if not isinstance(ctx, dict) or not ctx.get("filename"):
                raise CSpyError(
                    "No current project. Load a workspace or project first, or pass the project path explicitly."
                )

        if configuration:
            return ctx, str(configuration)

        current = to_plain(self.call("GetCurrentConfiguration", ctx))
        name = current.get("name") if isinstance(current, dict) else None
        if not name:
            raise CSpyError(
                f"No current configuration for project {ctx.get('filename')!r}; pass the configuration explicitly."
            )
        return ctx, str(name)

    def files(
        self, project: str | None = None, configuration: str | None = None, collection: str = "ProjFiles"
    ) -> list[str]:
        """Files of a project configuration; ``collection`` is one of :data:`FILE_COLLECTIONS`."""
        ctx, cfg_name = self.resolve(project, configuration)
        mod = self._client.idl.module("projectmanager.thrift")
        col = getattr(mod.FileCollectionType, str(collection), None)
        if col is None:
            valid = [n for n in dir(mod.FileCollectionType) if not n.startswith("_") and n[0].isupper()]
            raise CSpyError(f"Unknown collection {collection!r}; expected one of {valid}")
        return to_plain(self.call("GetFiles", ctx, cfg_name, col)) or []

    def build(self, project: str | None = None, configuration: str | None = None, jobs: int = 4) -> BuildResult:
        """Build synchronously. A failed build is a result, not an exception."""
        ctx, cfg_name = self.resolve(project, configuration)
        return self._build(ctx, cfg_name, jobs)

    def _build(self, ctx: dict[str, Any], cfg_name: str, jobs: int) -> BuildResult:
        result = to_plain(self.call("BuildProject", ctx, cfg_name, int(jobs)))
        result = result if isinstance(result, dict) else {}
        return BuildResult(
            project=ctx,
            configuration=cfg_name,
            # The field is spelled 'succeded' in projectmanager.thrift.
            succeeded=bool(result.get("succeded", False)),
            output=list(result.get("buildOutput") or []),
        )

    def launch_configuration(self, project: str | None = None, configuration: str | None = None) -> Any:
        """The backend's resolved ``shared.LaunchConfiguration`` for a configuration.

        Pass it to :meth:`Debugger.configure_resolved`; no launch.json needed.
        """
        ctx, cfg_name = self.resolve(project, configuration)
        return self.call("GetLaunchConfigurationForConfiguration", ctx, cfg_name)

    def start_debugging(
        self,
        project: str | None = None,
        configuration: str | None = None,
        *,
        build: bool = True,
        start: bool = True,
        jobs: int = 4,
    ) -> ProjectDebugResult:
        """Build (optionally), then configure and start a debug session for it.

        Stops after the build when it fails: check ``result.build.succeeded``.
        """
        ctx, cfg_name = self.resolve(project, configuration)
        result = ProjectDebugResult(project=ctx, configuration=cfg_name, build=None)
        if build:
            result.build = self._build(ctx, cfg_name, jobs)
            if not result.build.succeeded:
                return result

        result.launch_configuration = self.call("GetLaunchConfigurationForConfiguration", ctx, cfg_name)
        debugger = self._client.debugger
        debugger.configure_resolved(result.launch_configuration)
        result.configured = True
        if start:
            debugger.start()
            result.started = True
        return result
