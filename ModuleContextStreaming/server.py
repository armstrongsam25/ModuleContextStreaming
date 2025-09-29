# In ModuleContextStreaming/server.py
"""
Provides a reusable Server class for running a secure, authenticated gRPC service.
Now supports connecting to MCP (Model Context Protocol) servers as backends.
"""
import sys
import traceback
import asyncio
import json
from concurrent import futures
from typing import Dict, List, Optional, Any, AsyncIterator
import grpc
from google.protobuf.json_format import MessageToDict

from . import mcs_pb2, mcs_pb2_grpc
from .auth import KeycloakAuthenticator, AuthInterceptor

try:
	from mcp import ClientSession, StdioServerParameters
	from mcp.client.stdio import stdio_client

	MCP_AVAILABLE = True
except ImportError:
	MCP_AVAILABLE = False
	print("⚠️  Warning: MCP library not installed. MCP backend support disabled.", file=sys.stderr)
	print("   Install with: pip install mcp", file=sys.stderr)


class MCPToolAdapter:
	"""Wraps an MCP server connection and exposes its tools in MCS format."""

	def __init__(self, name: str, server_params: 'StdioServerParameters'):
		if not MCP_AVAILABLE:
			raise ImportError("MCP library is required for MCP backend support")

		self.name = name
		self.server_params = server_params
		self.session: Optional['ClientSession'] = None
		self.tools_cache: Dict[str, Any] = {}
		self._loop = None
		self._context = None
		self.read = None
		self.write = None

	async def connect(self):
		"""Initialize connection to MCP server."""
		print(f"🔌 Connecting to MCP backend: {self.name}")
		try:
			# stdio_client returns an async context manager
			self._context = stdio_client(self.server_params)
			self.read, self.write = await self._context.__aenter__()

			self.session = ClientSession(self.read, self.write)

			await self.session.initialize()

			# Fetch available tools from MCP server
			result = await self.session.list_tools()
			self.tools_cache = {tool.name: tool for tool in result.tools}
			print(f"✅ Connected to MCP backend '{self.name}' with {len(self.tools_cache)} tools")

		except Exception as e:
			print(f"❌ Failed to connect to MCP backend '{self.name}': {e}", file=sys.stderr)
			traceback.print_exc()
			raise

	def get_mcs_tools(self) -> Dict[str, callable]:
		"""Convert MCP tools to MCS tool registry format."""
		registry = {}

		for tool_name, tool_def in self.tools_cache.items():
			# Prefix with backend name to avoid conflicts
			prefixed_name = f"{self.name}:{tool_name}"

			# Create a closure that captures the tool details
			def make_tool_function(name: str, backend_name: str):
				def tool_function(arguments: Dict[str, Any]):
					"""Generated tool function for MCP tool (streaming)."""
					# Run async MCP call in the event loop
					if self._loop is None:
						self._loop = asyncio.new_event_loop()

					async def call_mcp_tool():
						result = await self.session.call_tool(name, arguments)
						return result

					try:
						result = self._loop.run_until_complete(call_mcp_tool())

						# Stream the results as chunks
						for content in result.content:
							if content.type == "text":
								yield content.text
							elif content.type == "image":
								# Decode base64 and yield as bytes
								import base64
								yield base64.b64decode(content.data)
							elif content.type == "resource":
								# Handle embedded resources
								if hasattr(content.resource, 'text'):
									yield f"[Resource: {content.resource.uri}]\n{content.resource.text}"
								elif hasattr(content.resource, 'blob'):
									import base64
									yield base64.b64decode(content.resource.blob)

						# Handle structured content if present
						if hasattr(result, 'structuredContent') and result.structuredContent:
							yield f"\n[Structured Output]\n{json.dumps(result.structuredContent, indent=2)}"

						# Handle error state
						if hasattr(result, 'isError') and result.isError:
							yield f"\n[Error: Tool execution failed]"

					except Exception as e:
						yield f"[MCP Error: {str(e)}]"

				# Set proper documentation
				tool_function.__doc__ = tool_def.description or f"MCP Tool from '{backend_name}': {name}"
				return tool_function

			registry[prefixed_name] = make_tool_function(tool_name, self.name)

		return registry

	async def close(self):
		"""Close the MCP connection."""
		try:
			if self.session:
				await self.session.close()
			if self._context:
				await self._context.__aexit__(None, None, None)
			print(f"🔌 Closed MCP backend connection: {self.name}")
		except Exception as e:
			print(f"⚠️  Error closing MCP connection '{self.name}': {e}", file=sys.stderr)


class ModuleContextServicer(mcs_pb2_grpc.ModuleContextServicer):
	"""Provides the gRPC method implementations using a tool registry."""

	def __init__(self, tool_registry):
		self.tool_registry = tool_registry
		super().__init__()

	def ListTools(self, request, context):
		"""Dynamically lists tools from the injected registry."""
		print("Received ListTools request.")
		try:
			tools = [
				mcs_pb2.ToolDefinition(name=name, description=func.__doc__ or "No description available.")
				for name, func in self.tool_registry.items()
			]
			return mcs_pb2.ListToolsResult(tools=tools)
		except Exception as e:
			print(f"❌ An unexpected error occurred in ListTools: {e}", file=sys.stderr)
			context.abort(grpc.StatusCode.INTERNAL, "An internal server error occurred.")

	def CallTool(self, request, context):
		"""Dispatches a tool call using the injected registry."""
		print(f"Dispatching CallTool request for tool: {request.tool_name}")
		tool_function = self.tool_registry.get(request.tool_name)

		if not tool_function:
			context.abort(grpc.StatusCode.NOT_FOUND, f"Tool '{request.tool_name}' not found.")
			return

		arguments = MessageToDict(request.arguments)
		sequence_id = 0

		try:
			for result_chunk in tool_function(arguments):
				chunk_kwargs = {'sequence_id': sequence_id}

				if isinstance(result_chunk, bytes):
					# Binary data (images, etc.)
					chunk_kwargs['image'] = mcs_pb2.ImageBlock(data=result_chunk, mime_type="image/jpeg")
				elif isinstance(result_chunk, dict):
					# Structured JSON data
					chunk_kwargs['text'] = mcs_pb2.TextBlock(text=json.dumps(result_chunk, indent=2))
				else:
					# Text data
					chunk_kwargs['text'] = mcs_pb2.TextBlock(text=str(result_chunk))

				yield mcs_pb2.ToolCallChunk(**chunk_kwargs)
				sequence_id += 1

		except Exception as e:
			print(f"❌ Error during tool execution '{request.tool_name}': {e}", file=sys.stderr)
			# Send error as final chunk
			yield mcs_pb2.ToolCallChunk(
				sequence_id=sequence_id,
				text=mcs_pb2.TextBlock(text=f"[Error: {str(e)}]")
			)


class Server:
	"""A configurable gRPC server for the ModuleContextStreaming service."""

	def __init__(self, tool_registry=None, port=50051, keycloak_url=None, keycloak_realm=None,
				 keycloak_audience=None, key_path=None, cert_path=None, mcp_backends=None):
		"""
		Initializes the Server with all necessary configuration.

		Args:
			tool_registry (dict): Maps tool names to their implementation functions.
			port (int): The port number to listen on.
			keycloak_url (str): The base URL of the Keycloak server.
			keycloak_realm (str): The Keycloak realm.
			keycloak_audience (str): The Keycloak audience for token validation.
			key_path (str): Path to the server's private key file.
			cert_path (str): Path to the server's certificate file.
			mcp_backends (list): List of MCP backend configurations
				[{"name": "fs", "command": "node", "args": ["server.js"], "env": {...}}]
		"""
		self.tool_registry = tool_registry or {}
		self.port = port
		self.keycloak_url = keycloak_url
		self.keycloak_realm = keycloak_realm
		self.keycloak_audience = keycloak_audience
		self.key_path = key_path
		self.cert_path = cert_path
		self.mcp_adapters: List[MCPToolAdapter] = []

		# Initialize MCP backends if provided
		if mcp_backends and MCP_AVAILABLE:
			self._initialize_mcp_backends(mcp_backends)
		elif mcp_backends and not MCP_AVAILABLE:
			print("⚠️  Warning: MCP backends specified but MCP library not available", file=sys.stderr)

	def _initialize_mcp_backends(self, mcp_backends: List[Dict[str, Any]]):
		"""Initialize connections to MCP backends."""
		print(f"🚀 Initializing {len(mcp_backends)} MCP backend(s)...")

		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)

		for mcp_config in mcp_backends:
			try:
				name = mcp_config.get("name", "unnamed")
				adapter = MCPToolAdapter(
					name=name,
					server_params=StdioServerParameters(
						command=mcp_config["command"],
						args=mcp_config.get("args", []),
						env=mcp_config.get("env")
					)
				)

				loop.run_until_complete(adapter.connect())
				self.mcp_adapters.append(adapter)

				# Merge MCP tools into registry
				mcp_tools = adapter.get_mcs_tools()
				self.tool_registry.update(mcp_tools)
				print(f"✅ Loaded {len(mcp_tools)} tools from MCP backend '{name}'")

			except Exception as e:
				print(f"❌ Failed to initialize MCP backend '{mcp_config.get('name', 'unknown')}': {e}", file=sys.stderr)
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
				# Secure Mode
				with open(self.key_path, 'rb') as f:
					private_key = f.read()
				with open(self.cert_path, 'rb') as f:
					certificate_chain = f.read()
				server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
				server.add_secure_port(f'[::]:{self.port}', server_credentials)
				print(f"✅ Secure server started, listening on port {self.port}.")
			else:
				# Insecure Mode
				server.add_insecure_port(f'[::]:{self.port}')
				print("⚠️  WARNING: Server is running in INSECURE mode. Do not use in production.")
				print(f"✅ Insecure server started, listening on port {self.port}.")

			server.start()

			try:
				server.wait_for_termination()
			except KeyboardInterrupt:
				print("\n🛑 Shutting down server...")
				self._cleanup()

		except FileNotFoundError as e:
			print(f"❌ Error: Certificate file not found: {e.filename}", file=sys.stderr)
			sys.exit(1)
		except Exception as e:
			print(f"❌ An error occurred during server startup: {e}", file=sys.stderr)
			print(traceback.format_exc(), file=sys.stderr)
			sys.exit(1)

	def _cleanup(self):
		"""Cleanup MCP connections."""
		if self.mcp_adapters:
			loop = asyncio.new_event_loop()
			asyncio.set_event_loop(loop)
			for adapter in self.mcp_adapters:
				try:
					loop.run_until_complete(adapter.close())
				except Exception as e:
					print(f"⚠️  Error closing MCP adapter: {e}", file=sys.stderr)