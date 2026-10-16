"""Local-only Streamable HTTP server with explicitly injected catalog transport."""

import json
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
from mcp.types import CallToolResult, TextContent
from pydantic import Field
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from apple_music_mcp.auth import AuthConfig
from apple_music_mcp.client import AppleMusicClient, AppleMusicError, validate_search_input
from apple_music_mcp.logging import LOGGER, RequestLoggingMiddleware, configure_logging, operation

from apple_music_mcp.web_session import WebSessionAuth, WebSessionClient, WebSessionError

HOST = "127.0.0.1"
PORT = 8765


class LoggedFastMCP(FastMCP):
    def streamable_http_app(self):
        app = super().streamable_http_app()
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def logged_lifespan(application):
            with operation("server"):
                async with AsyncExitStack() as stack:
                    catalog = getattr(self, "catalog", None)
                    if catalog is not None:
                        await stack.enter_async_context(catalog)
                    web_session = getattr(self, "web_session", None)
                    if web_session is not None:
                        await stack.enter_async_context(web_session)
                    state = await stack.enter_async_context(original_lifespan(application))
                    LOGGER.info("startup", extra={"operation": "server"})
                    try:
                        yield state
                    finally:
                        LOGGER.info("shutdown", extra={"operation": "server"})

        app.router.lifespan_context = logged_lifespan
        app.add_middleware(RequestLoggingMiddleware)
        return app

    async def run_streamable_http_async(self):
        import uvicorn

        config = uvicorn.Config(
            self.streamable_http_app(), host=self.settings.host,
            port=self.settings.port, log_config=None, access_log=False,
        )
        await uvicorn.Server(config).serve()


def create_server(
    config: AuthConfig | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
    web_session_auth: WebSessionAuth | None = None,
    web_session_transport: httpx.AsyncBaseTransport | None = None,
    web_session_enabled: bool = False,
    web_session_live_opt_in: bool = False,
) -> FastMCP:
    """Build a fixed-loopback server with offline configuration diagnostics."""
    config = AuthConfig.from_env() if config is None else config
    catalog = AppleMusicClient(config, transport=transport) if transport is not None else None

    if type(web_session_live_opt_in) is not bool:
        raise WebSessionError("INVALID_HTTP_CONFIG")
    if type(web_session_enabled) is not bool:
        raise WebSessionError("INVALID_INPUT")
    if web_session_auth is not None and type(web_session_auth) is not WebSessionAuth:
        raise WebSessionError("INVALID_INPUT")
    web_session = (
        WebSessionClient(
            web_session_auth if web_session_auth is not None else WebSessionAuth(),
            transport=web_session_transport, enabled=web_session_enabled,
            live_opt_in=web_session_live_opt_in,
        ) if web_session_transport is not None else None
    )

    server = LoggedFastMCP(
        "Apple Music REST scaffold",
        host=HOST,
        port=PORT,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[f"{HOST}:{PORT}"],
            allowed_origins=[f"http://{HOST}:{PORT}"],
        ),
    )

    server.catalog = catalog
    server.web_session = web_session

    @server.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
    )
    def diagnostics() -> dict[str, object]:
        """Return offline configuration status without exposing credential values."""
        auth = config.diagnostics()
        with operation("diagnostics", error_code=auth["error_code"]):
            return {
                "status": "ok",
                "transport": "streamable-http",
                "apple_api_enabled": False,
                "credentials_required": False,
                "auth": auth,
            }

    @server.tool(annotations={
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": True,
    })
    async def search_songs(
        storefront: Annotated[Any, Field(description="Two lowercase ASCII letters", json_schema_extra={"type": "string", "pattern": "^[a-z]{2}$"})] = None,
        term: Annotated[Any, Field(description="Trimmed search term, no control characters", json_schema_extra={"type": "string", "minLength": 1, "maxLength": 100})] = None,
        limit: Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": 1, "maximum": 25})] = 10,
    ) -> CallToolResult:
        """Search one page of catalog songs. Requires an explicitly injected transport.

        Validation runs here without SDK coercion or value-bearing validation errors.
        """
        try:
            validate_search_input(storefront, term, limit)
            state = config.diagnostics()["developer_token"]
            if state != "configured":
                raise AppleMusicError("AUTH_CONFIG_MISSING" if state == "missing" else "AUTH_CONFIG_INVALID")
            if transport is None:
                raise AppleMusicError("TRANSPORT_NOT_CONFIGURED")
            result = await catalog.search_songs(storefront, term, limit=limit)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], structuredContent=result)
        except AppleMusicError as exc:
            return CallToolResult(isError=True, content=[TextContent(type="text", text=exc.code)])
        except Exception:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="INTERNAL_ERROR")])

    @server.tool(annotations={
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    })
    async def list_playlists(
        limit: Annotated[Any, Field(json_schema_extra={"type": "integer", "minimum": 1, "maximum": 25})] = 10,
    ) -> CallToolResult:
        """Read one playlist page; disabled unless explicitly enabled in Python."""
        try:
            if not web_session_enabled:
                raise WebSessionError("BACKEND_DISABLED")
            if type(limit) is not int or not 1 <= limit <= 25:
                raise WebSessionError("INVALID_INPUT")
            if web_session_auth is None:
                raise WebSessionError("AUTH_CONFIG_MISSING")
            web_session_auth.validate()
            if web_session is None:
                raise WebSessionError("MOCK_TRANSPORT_REQUIRED")
            result = await web_session.list_playlists(limit=limit)
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result))], structuredContent=result)
        except WebSessionError as exc:
            return CallToolResult(isError=True, content=[TextContent(type="text", text=exc.code)])
        except Exception:
            return CallToolResult(isError=True, content=[TextContent(type="text", text="INTERNAL_ERROR")])

    return server


def main() -> None:
    """Serve MCP at http://127.0.0.1:8765/mcp only."""
    configure_logging()
    try:
        with operation("server"):
            server = create_server()
            configure_logging()  # Restore level after SDK construction.
            server.run(transport="streamable-http")
    except KeyboardInterrupt:
        pass
    except Exception:
        # operation() already emitted sanitized diagnostics. Prevent Python's
        # default unhandled traceback from printing exception messages.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
