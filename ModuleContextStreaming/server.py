# In ModuleContextStreaming/server.py
"""
Provides a reusable Server class for running a secure, authenticated gRPC service.
"""
import asyncio
import sys
import traceback
from concurrent import futures
from typing import Any, Dict, List

import grpc

# Local module imports for the new structure
from .auth import AuthInterceptor, KeycloakAuthenticator
from .mcs_mcp_adapter import MCPToolAdapter
from .servicer import ModuleContextServicer
from . import mcs_pb2_grpc

# Conditional import for MCP library
try:
    from mcp import StdioServerParameters
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("⚠️  Warning: MCP library not installed. MCP backend support disabled.", file=sys.stderr)
    print("   Install with: pip install mcp", file=sys.stderr)


class Server:
    """A configurable gRPC server for the ModuleContextStreaming service."""

    def __init__(self, tool_registry=None, port=50051, keycloak_url=None, keycloak_realm=None,
                 keycloak_audience=None, key_path=None, cert_path=None, mcp_backends=None):
        self.tool_registry = tool_registry or {}
        self.port = port
        self.keycloak_url = keycloak_url
        self.keycloak_realm = keycloak_realm
        self.keycloak_audience = keycloak_audience
        self.key_path = key_path
        self.cert_path = cert_path
        self.mcp_adapters: List[MCPToolAdapter] = []

        if mcp_backends:
            self._initialize_mcp_backends(mcp_backends)

    def _initialize_mcp_backends(self, mcp_backends: List[Dict[str, Any]]):
        """Initialize connections to MCP backends."""
        if not MCP_AVAILABLE:
            print("⚠️  Warning: MCP backends specified but MCP library not available.", file=sys.stderr)
            return

        print(f"🚀 Initializing {len(mcp_backends)} MCP backend(s)...")
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)

        for config in mcp_backends:
            try:
                name = config.get("name", "unnamed")
                adapter = MCPToolAdapter(
                    name=name,
                    server_params=StdioServerParameters(
                        command=config["command"],
                        args=config.get("args", []),
                        env=config.get("env")
                    )
                )
                loop.run_until_complete(adapter.connect())
                self.mcp_adapters.append(adapter)

                mcp_tools = adapter.get_mcs_tools()
                self.tool_registry.update(mcp_tools)
                print(f"✅ Loaded {len(mcp_tools)} tools from MCP backend '{name}'")
            except Exception as e:
                print(f"❌ Failed to initialize MCP backend '{config.get('name', 'unknown')}': {e}", file=sys.stderr)
                traceback.print_exc()

        print(f"✅ Total tools available: {len(self.tool_registry)}")

    def run(self):
        """Starts the gRPC server and waits for termination."""
        try:
            authenticator = KeycloakAuthenticator(self.keycloak_url, self.keycloak_realm, self.keycloak_audience)
            auth_interceptor = AuthInterceptor(authenticator)
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=(auth_interceptor,))
            servicer_instance = ModuleContextServicer(self.tool_registry)
            mcs_pb2_grpc.add_ModuleContextServicer_to_server(servicer_instance, server)

            if self.key_path and self.cert_path:
                with open(self.key_path, 'rb') as f: private_key = f.read()
                with open(self.cert_path, 'rb') as f: certificate_chain = f.read()
                credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
                server.add_secure_port(f'[::]:{self.port}', credentials)
                print(f"✅ Secure server started on port {self.port}.")
            else:
                server.add_insecure_port(f'[::]:{self.port}')
                print(f"⚠️  Insecure server started on port {self.port}.")

            server.start()
            server.wait_for_termination()
        except KeyboardInterrupt:
            print("\n🛑 Shutting down server...")
        except FileNotFoundError as e:
            print(f"❌ Error: Certificate file not found: {e.filename}", file=sys.stderr)
        except Exception as e:
            print(f"❌ An error occurred during server startup: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
        finally:
            self._cleanup()

    def _cleanup(self):
        """Cleanup MCP connections on shutdown."""
        if self.mcp_adapters:
            print("🔌 Closing MCP connections...")
            loop = asyncio.get_event_loop_policy().new_event_loop()
            asyncio.set_event_loop(loop)
            for adapter in self.mcp_adapters:
                loop.run_until_complete(adapter.close())