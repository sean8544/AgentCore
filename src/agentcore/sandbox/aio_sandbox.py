"""AIO (All-in-One) Sandbox backend for AgentCore.

Wraps the ``agent_sandbox`` SDK to provide a ``SandboxBackendProtocol``-
compatible backend that talks to the AIO sandbox image
(``ghcr.io/agent-infra/sandbox``) running inside an OpenSandbox container.

The AIO image bundles browser, shell, file system, VSCode, Jupyter and
MCP server into a single container exposed on port 8080.  The
``agent_sandbox`` SDK provides a rich async-native client for all of
these capabilities.

Integration flow
----------------
1. OpenSandbox creates a Docker container from the AIO image.
2. ``SandboxSync.get_endpoint(8080)`` returns the HTTP URL for the
   AIO server running inside the container.
3. ``agent_sandbox.AsyncSandbox(base_url=endpoint)`` connects to it.
4. This adapter wraps the AsyncSandbox and exposes the
   ``SandboxBackendProtocol`` interface that deepagents middleware
   expects.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
)

logger = logging.getLogger(__name__)

# AIO internal server port (inside the container).
_AIO_INTERNAL_PORT = 8080


# ---------------------------------------------------------------------------
# AIO response envelope helpers
# ---------------------------------------------------------------------------
# The agent_sandbox SDK's high-level client returns the full response
# envelope ({success, message, data, hint}); the actual payload lives in
# ``data``. Error responses reuse HTTP 200 with success=False and an
# error-shaped ``data`` ({path, operation, message, error_type, ...}) that
# fails the SDK's success-schema validation — those ValidationErrors carry
# the original server dict in ``input`` so we can recover its message.


def _unwrap(result: Any) -> Any:
    """Return the envelope's ``data`` payload (or *result* if unwrapped)."""
    data = getattr(result, "data", None)
    return data if data is not None else result


def _envelope_error(result: Any) -> str | None:
    """Return the server message when the envelope reports failure."""
    if getattr(result, "success", True) is False:
        return getattr(result, "message", None) or "Sandbox operation failed"
    return None


def _friendly_error(exc: Exception) -> str:
    """Extract the server-provided message from an SDK exception.

    Error-shaped ``data`` payloads break the SDK's pydantic validation;
    the server dict (with its human-readable ``message``) is preserved in
    each validation error's ``input``.
    """
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            for err in errors():
                value = err.get("input")
                if isinstance(value, dict) and value.get("message"):
                    return str(value["message"])
        except Exception:
            pass
    return str(exc)


class AIOSandboxBackend(SandboxBackendProtocol):
    """Backend that delegates to the AIO sandbox via ``agent_sandbox`` SDK.

    The AIO sandbox provides:
    - ``shell.exec_command`` for command execution
    - ``file.read_file`` / ``file.write_file`` for file I/O
    - ``file.replace_in_file`` for string replacement
    - ``file.glob_files`` / ``file.grep_files`` for search
    - ``file.list_path`` for directory listing
    - ``file.upload_file`` / ``file.download_file`` for streaming transfer
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: int = 600,
    ) -> None:
        """Initialise the AIO backend.

        Parameters
        ----------
        sandbox_id:
            OpenSandbox container id (used as ``self.id``).
        base_url:
            HTTP URL for the AIO server (obtained from
            ``SandboxSync.get_endpoint(8080)``).
        headers:
            Optional headers required by the endpoint.
        timeout:
            Default command timeout in seconds.
        """
        self._sandbox_id = sandbox_id
        self._base_url = base_url
        self._headers = headers or {}
        self._default_timeout = timeout
        self._client: Any = None  # Lazy-initialised AsyncSandbox

    # -- lazy client initialisation ----------------------------------------

    def _get_client(self) -> Any:
        """Return the ``AsyncSandbox`` client, creating it on first use."""
        if self._client is None:
            from agent_sandbox import AsyncSandbox

            self._client = AsyncSandbox(
                base_url=self._base_url,
                headers=self._headers or None,
                timeout=float(self._default_timeout),
            )
        return self._client

    # -- SandboxBackendProtocol: identity ----------------------------------

    @property
    def id(self) -> str:
        """Return the OpenSandbox sandbox id."""
        return self._sandbox_id

    # -- SandboxBackendProtocol: execute -----------------------------------

    async def aexecute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command inside the AIO sandbox."""
        client = self._get_client()
        effective_timeout = timeout if timeout is not None else self._default_timeout
        try:
            result = await client.shell.exec_command(
                command=command,
                timeout=float(effective_timeout),
            )
        except Exception as exc:
            return ExecuteResponse(
                output=_friendly_error(exc), exit_code=-1, truncated=False
            )
        error = _envelope_error(result)
        if error:
            return ExecuteResponse(output=error, exit_code=-1, truncated=False)
        # The SDK returns the response envelope; the command payload
        # (output / exit_code / status) lives inside ``data``.
        payload = _unwrap(result)
        output = getattr(payload, "output", "") or ""
        exit_code = getattr(payload, "exit_code", 0) or 0
        status = getattr(payload, "status", "") or ""
        # AIO returns status like "completed", "timeout", etc.
        truncated = status == "TRUNCATED"
        return ExecuteResponse(
            output=output,
            exit_code=exit_code,
            truncated=truncated,
        )

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Sync version of execute — runs async in a thread."""
        return asyncio.run(self.aexecute(command, timeout=timeout))

    # -- BackendProtocol: file read/write/edit/delete ----------------------

    async def aread(
        self,
        file_path: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """Read a file from the AIO sandbox."""
        from deepagents.backends.protocol import FileData, ReadResult

        client = self._get_client()
        try:
            # AIO uses 0-based start_line, end_line is exclusive
            start_line = offset or 0
            end_line = (start_line + limit) if limit else None
            kwargs: dict[str, Any] = {"file": file_path, "start_line": start_line}
            if end_line is not None:
                kwargs["end_line"] = end_line
            result = await client.file.read_file(**kwargs)
            error = _envelope_error(result)
            if error:
                return ReadResult(error=error, file_data=None)
            # The SDK returns the response envelope; content lives in ``data``.
            content = getattr(_unwrap(result), "content", "") or ""
            return ReadResult(
                error=None,
                file_data=FileData(content=content, encoding="utf-8"),
            )
        except Exception as exc:
            return ReadResult(error=_friendly_error(exc), file_data=None)

    async def awrite(self, file_path: str, content: str) -> Any:
        """Write a file into the AIO sandbox."""
        from deepagents.backends.protocol import WriteResult

        client = self._get_client()
        try:
            # Ensure parent directory exists
            parent = file_path.rsplit("/", 1)[0] or "/"
            await client.shell.exec_command(command=f"mkdir -p {parent}")
            result = await client.file.write_file(file=file_path, content=content)
            error = _envelope_error(result)
            if error:
                return WriteResult(error=error)
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=_friendly_error(exc))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> Any:
        """Edit a file by replacing text occurrences."""
        from deepagents.backends.protocol import EditResult

        client = self._get_client()
        try:
            # Read current content
            read_result = await client.file.read_file(file=file_path)
            error = _envelope_error(read_result)
            if error:
                return EditResult(error=error)
            text = getattr(_unwrap(read_result), "content", "") or ""
            if replace_all:
                new_text = text.replace(old_string, new_string)
                occurrences = text.count(old_string)
            else:
                new_text = text.replace(old_string, new_string, 1)
                occurrences = 1 if old_string in text else 0
            write_result = await client.file.write_file(
                file=file_path, content=new_text
            )
            error = _envelope_error(write_result)
            if error:
                return EditResult(error=error)
            return EditResult(path=file_path, occurrences=occurrences)
        except Exception as exc:
            return EditResult(error=_friendly_error(exc))

    async def adelete(self, file_path: str) -> Any:
        """Delete a file from the AIO sandbox."""
        from deepagents.backends.protocol import DeleteResult

        client = self._get_client()
        try:
            await client.shell.exec_command(command=f"rm -f {file_path}")
            return DeleteResult(path=file_path)
        except Exception as exc:
            return DeleteResult(error=str(exc))

    # -- BackendProtocol: grep/glob/ls -------------------------------------

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> Any:
        """Search file contents using AIO's grep API."""
        from deepagents.backends.protocol import GrepResult, GrepMatch

        client = self._get_client()
        search_path = path or "."
        try:
            kwargs: dict[str, Any] = {
                "path": search_path,
                "pattern": pattern,
                "recursive": True,
            }
            if max_count:
                kwargs["max_results"] = max_count
            if glob:
                kwargs["include"] = [glob]
            result = await client.file.grep_files(**kwargs)
            error = _envelope_error(result)
            if error:
                return GrepResult(error=error)
            payload = _unwrap(result)
            matches: list[GrepMatch] = []
            for match in getattr(payload, "matches", []) or []:
                matches.append(GrepMatch(
                    path=getattr(match, "file", getattr(match, "path", "")),
                    line=getattr(match, "line", 0),
                    text=getattr(match, "content", getattr(match, "text", "")),
                ))
            return GrepResult(matches=matches, truncated=False)
        except Exception as exc:
            return GrepResult(error=_friendly_error(exc))

    async def aglob(
        self,
        pattern: str,
        path: str | None = None,
    ) -> Any:
        """File glob matching using AIO's glob API."""
        from deepagents.backends.protocol import GlobResult, FileInfo

        client = self._get_client()
        search_path = path or "/"
        try:
            result = await client.file.glob_files(
                path=search_path,
                pattern=pattern,
            )
            error = _envelope_error(result)
            if error:
                return GlobResult(error=error)
            payload = _unwrap(result)
            matches: list[FileInfo] = []
            for f in getattr(payload, "files", []) or []:
                file_path = getattr(f, "path", getattr(f, "name", ""))
                is_dir = getattr(f, "is_directory", False)
                size = getattr(f, "size", 0) or 0
                matches.append(FileInfo(path=file_path, is_dir=is_dir, size=size))
            return GlobResult(matches=matches, truncated=False)
        except Exception as exc:
            return GlobResult(error=_friendly_error(exc))

    async def als(self, path: str) -> Any:
        """List directory contents using AIO's list API."""
        from deepagents.backends.protocol import LsResult, FileInfo

        client = self._get_client()
        try:
            result = await client.file.list_path(
                path=path,
                recursive=False,
                show_hidden=False,
                include_size=True,
                include_permissions=True,
            )
            error = _envelope_error(result)
            if error:
                return LsResult(error=error)
            payload = _unwrap(result)
            # AIO's list payload uses ``files`` for entries and
            # ``is_directory`` on each item.
            entries: list[FileInfo] = []
            for item in getattr(payload, "files", []) or []:
                item_path = getattr(item, "path", getattr(item, "name", ""))
                is_dir = getattr(item, "is_directory", False)
                size = getattr(item, "size", 0) or 0
                entries.append(FileInfo(path=item_path, is_dir=is_dir, size=size))
            return LsResult(entries=entries)
        except Exception as exc:
            return LsResult(error=_friendly_error(exc))

    # -- BackendProtocol: upload/download files ----------------------------

    async def aupload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        """Upload files to the AIO sandbox."""
        client = self._get_client()
        results: list[FileUploadResponse] = []
        for path, content in files:
            try:
                # Ensure parent directory exists
                parent = path.rsplit("/", 1)[0] or "/"
                await client.shell.exec_command(command=f"mkdir -p {parent}")
                # Use base64 encoding for binary upload
                b64_content = base64.b64encode(content).decode("ascii")
                result = await client.file.write_file(
                    file=path,
                    content=b64_content,
                    encoding="base64",
                )
                error = _envelope_error(result)
                if error:
                    results.append(FileUploadResponse(path=path, error=error))
                else:
                    results.append(FileUploadResponse(path=path, error=None))
            except Exception as exc:
                results.append(
                    FileUploadResponse(path=path, error=_friendly_error(exc))
                )
        return results

    async def adownload_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        """Download files from the AIO sandbox."""
        client = self._get_client()
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                # Download as streaming bytes
                chunks: list[bytes] = []
                async for chunk in client.file.download_file(path=path):
                    chunks.append(chunk)
                content = b"".join(chunks)
                results.append(FileDownloadResponse(
                    path=path,
                    content=content,
                    error=None,
                ))
            except Exception as exc:
                err_msg = str(exc).lower()
                if "not found" in err_msg or "no such file" in err_msg:
                    error = "file_not_found"
                elif "permission" in err_msg or "denied" in err_msg:
                    error = "permission_denied"
                else:
                    error = "file_not_found"
                results.append(FileDownloadResponse(
                    path=path,
                    content=None,
                    error=error,
                ))
        return results

    # -- Sync wrappers (required by BackendProtocol base) ------------------
    # The deepagents middleware primarily calls async methods (als, aread,
    # etc.) which we implement natively above.  The sync methods below are
    # fallbacks for any code paths that call them directly.

    def ls(self, path: str) -> Any:
        return asyncio.run(self.als(path))

    def read(self, file_path: str, **kwargs: Any) -> Any:
        return asyncio.run(self.aread(file_path, **kwargs))

    def write(self, file_path: str, content: str) -> Any:
        return asyncio.run(self.awrite(file_path, content))

    def edit(self, file_path: str, old_string: str, new_string: str, **kwargs: Any) -> Any:
        return asyncio.run(self.aedit(file_path, old_string, new_string, **kwargs))

    def delete(self, file_path: str) -> Any:
        return asyncio.run(self.adelete(file_path))

    def grep(self, pattern: str, path: str | None = None, **kwargs: Any) -> Any:
        return asyncio.run(self.agrep(pattern, path, **kwargs))

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return asyncio.run(self.aglob(pattern, path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return asyncio.run(self.aupload_files(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return asyncio.run(self.adownload_files(paths))
