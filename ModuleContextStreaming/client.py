# In ModuleContextStreaming/client.py
"""
Provides a reusable Client class for connecting to the ModuleContextStreaming service.
Enhanced with MCP-aware content parsing and handling.
"""
import json
import sys
from typing import Dict, List, Any, Optional, Iterator

import grpc
import requests
from google.protobuf.json_format import ParseDict

from . import mcs_pb2, mcs_pb2_grpc


class MCPContentParser:
	"""Helper class to parse and categorize MCP content from tool responses."""

	@staticmethod
	def parse_tool_response(chunks: Iterator) -> Dict[str, Any]:
		"""
		Parse tool response chunks and categorize by content type.

		Args:
			chunks: Iterator of ToolCallChunk messages

		Returns:
			dict with keys: 'text', 'images', 'structured', 'resources', 'errors', 'raw_chunks'
		"""
		result = {
			'text': [],
			'images': [],
			'structured': [],
			'resources': [],
			'errors': [],
			'raw_chunks': []
		}

		for chunk in chunks:
			result['raw_chunks'].append(chunk)

			if chunk.HasField('text'):
				text = chunk.text.text

				# Detect structured output
				if '[Structured Output]' in text:
					try:
						# Extract JSON from structured output marker
						parts = text.split('[Structured Output]', 1)
						if len(parts) > 1:
							structured_json = parts[1].strip()
							structured = json.loads(structured_json)
							result['structured'].append(structured)
							# Keep the text before the marker if any
							if parts[0].strip():
								result['text'].append(parts[0].strip())
							continue
					except json.JSONDecodeError:
						pass  # Fall through to regular text handling

				# Detect resource content
				if text.startswith('[Resource:'):
					try:
						# Extract resource URI and content
						lines = text.split('\n', 1)
						uri = lines[0].replace('[Resource:', '').replace(']', '').strip()
						content = lines[1] if len(lines) > 1 else ""
						result['resources'].append({
							'uri': uri,
							'content': content
						})
						continue
					except:
						pass  # Fall through to regular text handling

				# Detect errors
				if text.startswith('[Error:') or text.startswith('[MCP Error:'):
					result['errors'].append(text)
					continue

				# Regular text
				result['text'].append(text)

			elif chunk.HasField('image'):
				result['images'].append({
					'data': chunk.image.data,
					'mime_type': chunk.image.mime_type
				})

		return result

	@staticmethod
	def get_combined_text(parsed_result: Dict[str, Any]) -> str:
		"""Get all text content combined into a single string."""
		return '\n'.join(parsed_result['text'])

	@staticmethod
	def save_images(parsed_result: Dict[str, Any], output_dir: str = '.', prefix: str = 'image') -> List[str]:
		"""
		Save all images from parsed result to disk.

		Args:
			parsed_result: Result from parse_tool_response
			output_dir: Directory to save images
			prefix: Filename prefix

		Returns:
			List of saved file paths
		"""
		import os
		saved_paths = []

		for idx, img in enumerate(parsed_result['images']):
			# Determine extension from MIME type
			mime_type = img['mime_type']
			ext = mime_type.split('/')[-1] if '/' in mime_type else 'jpg'

			filename = f"{prefix}_{idx}.{ext}"
			filepath = os.path.join(output_dir, filename)

			with open(filepath, 'wb') as f:
				f.write(img['data'])

			saved_paths.append(filepath)
			print(f"💾 Saved image: {filepath}")

		return saved_paths


class Client:
	"""A gRPC client for the ModuleContextStreaming service."""

	def __init__(self, server_address, keycloak_url, keycloak_realm, keycloak_client_id, keycloak_client_secret,
				 keycloak_audience, cert_path=None):
		"""
		Initializes and connects the client.

		Args:
			server_address (str): The address of the gRPC server (e.g., 'localhost:50051').
			keycloak_url (str): The base URL of the Keycloak server.
			keycloak_realm (str): The Keycloak realm.
			keycloak_client_id (str): The client ID for authentication.
			keycloak_client_secret (str): The client secret for authentication.
			keycloak_audience (str): The audience for the token.
			cert_path (str, optional): Path to a specific server certificate file for TLS.
									   If None (default), the system's default trust store is used for production.
		"""
		self.server_address = server_address
		self.auth_metadata = None
		self.channel = None
		self.stub = None
		self.parser = MCPContentParser()

		print("🚀 Initializing MCS Client...")
		print("🔐 Authenticating with Keycloak...")
		jwt_token = self._get_keycloak_token(keycloak_url, keycloak_realm, keycloak_client_id, keycloak_client_secret,
											 keycloak_audience)
		if not jwt_token:
			raise ConnectionError("Failed to authenticate with Keycloak.")
		print("✅ Successfully authenticated.")
		self.auth_metadata = [('authorization', f'Bearer {jwt_token}')]

		if cert_path:
			# Secure Mode
			print(f"🔒 HINT: Using custom certificate for secure connection: {cert_path}")
			try:
				with open(cert_path, 'rb') as f:
					trusted_certs = f.read()
				credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
				self.channel = grpc.secure_channel(self.server_address, credentials)
			except FileNotFoundError:
				raise FileNotFoundError(f"Certificate file not found at '{cert_path}'.")
		else:
			# Insecure Mode
			print("⚠️  WARNING: Client connecting via an INSECURE channel. Do not use in production.")
			self.channel = grpc.insecure_channel(self.server_address)

		self.stub = mcs_pb2_grpc.ModuleContextStub(self.channel)

	def _get_keycloak_token(self, url, realm, client_id, client_secret, audience):
		"""Fetches an access token from Keycloak."""
		token_url = f"{url}/realms/{realm}/protocol/openid-connect/token"
		payload = {
			"grant_type": "client_credentials", "client_id": client_id,
			"client_secret": client_secret, "audience": audience
		}
		try:
			response = requests.post(token_url, data=payload, timeout=10)
			response.raise_for_status()
			return response.json()["access_token"]
		except requests.exceptions.RequestException as e:
			print(f"❌ Could not get token from Keycloak: {e}", file=sys.stderr)
			return None

	def list_tools(self, verbose=True):
		"""
		Requests the list of available tools from the server.

		Args:
			verbose (bool): Whether to print tool information

		Returns:
			list: A list of ToolDefinition protobuf messages, or an empty list on error.
		"""
		try:
			if verbose:
				print("\n----- Listing Available Tools -----")
			request = mcs_pb2.ListToolsRequest()
			response = self.stub.ListTools(request, metadata=self.auth_metadata)

			if verbose:
				print(f"✅ Found {len(response.tools)} tools available from server:")
				for tool in response.tools:
					# Check if it's an MCP tool (has prefix)
					if ':' in tool.name:
						backend, tool_name = tool.name.split(':', 1)
						print(f"  📦 [{backend}] {tool_name}")
					else:
						print(f"  🔧 {tool.name}")
					if tool.description:
						print(f"     {tool.description}")

			return response.tools
		except grpc.RpcError as e:
			print(f"❌ Error listing tools: {e.code().name}: {e.details()}", file=sys.stderr)
			return []

	def call_tool(self, tool_name, arguments_dict, verbose=True):
		"""
		Performs a tool call and yields the streamed response chunks.

		Args:
			tool_name (str): The name of the tool to execute.
			arguments_dict (dict): A dictionary of arguments for the tool.
			verbose (bool): Whether to print progress information

		Yields:
			ToolCallChunk: A protobuf message for each chunk of the response.
		"""
		try:
			if verbose:
				print(f"\n----- Calling Tool: {tool_name} -----")
				print(f"Arguments: {arguments_dict}")

			arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
			ParseDict(arguments_dict, arguments_struct)
			stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name, arguments=arguments_struct)

			for chunk in self.stub.CallTool(stream_request, metadata=self.auth_metadata):
				yield chunk

			if verbose:
				print("✅ Stream finished.")
		except grpc.RpcError as e:
			print(f"❌ Error during CallTool ({tool_name}): {e.code().name}: {e.details()}", file=sys.stderr)

	def call_tool_parsed(self, tool_name: str, arguments_dict: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
		"""
		Call a tool and automatically parse different content types.

		This is especially useful for MCP tools that return mixed content.

		Args:
			tool_name: The name of the tool to execute
			arguments_dict: A dictionary of arguments for the tool
			verbose: Whether to print progress information

		Returns:
			dict with keys: 'text', 'images', 'structured', 'resources', 'errors', 'raw_chunks'
		"""
		chunks = self.call_tool(tool_name, arguments_dict, verbose=verbose)
		result = self.parser.parse_tool_response(chunks)

		if verbose and result['errors']:
			print(f"⚠️  Tool returned {len(result['errors'])} error(s)")
			for error in result['errors']:
				print(f"   {error}")

		return result

	def call_tool_simple(self, tool_name: str, arguments_dict: Dict[str, Any]) -> str:
		"""
		Call a tool and return all text content as a single string.

		This is a convenience method for simple text-only tool calls.

		Args:
			tool_name: The name of the tool to execute
			arguments_dict: A dictionary of arguments for the tool

		Returns:
			str: Combined text content from all chunks
		"""
		parsed = self.call_tool_parsed(tool_name, arguments_dict, verbose=False)
		return self.parser.get_combined_text(parsed)

	def get_mcp_tools(self, backend_name: Optional[str] = None) -> List[Any]:
		"""
		Get tools from MCP backends.

		Args:
			backend_name: Optional backend name to filter by (e.g., "fs", "github")
						 If None, returns all tools

		Returns:
			List of tool definitions
		"""
		all_tools = self.list_tools(verbose=False)

		if backend_name:
			return [t for t in all_tools if t.name.startswith(f"{backend_name}:")]
		else:
			return [t for t in all_tools if ':' in t.name]

	def get_native_tools(self) -> List[Any]:
		"""Get tools that are not from MCP backends (native Python tools)."""
		all_tools = self.list_tools(verbose=False)
		return [t for t in all_tools if ':' not in t.name]

	def save_tool_images(self, tool_name: str, arguments_dict: Dict[str, Any],
						 output_dir: str = '.', prefix: Optional[str] = None) -> List[str]:
		"""
		Call a tool and save any images it returns.

		Args:
			tool_name: The name of the tool to execute
			arguments_dict: A dictionary of arguments for the tool
			output_dir: Directory to save images
			prefix: Filename prefix (defaults to tool name)

		Returns:
			List of saved file paths
		"""
		if prefix is None:
			prefix = tool_name.replace(':', '_')

		parsed = self.call_tool_parsed(tool_name, arguments_dict, verbose=True)

		if not parsed['images']:
			print("ℹ️  No images returned by tool")
			return []

		return self.parser.save_images(parsed, output_dir, prefix)

	def close(self):
		"""Closes the gRPC channel."""
		if self.channel:
			self.channel.close()
			print("🔌 Client connection closed.")


# Convenience function for quick testing
def quick_connect(server_address: str = "localhost:50051",
				  keycloak_url: str = None,
				  keycloak_realm: str = None,
				  keycloak_client_id: str = None,
				  keycloak_client_secret: str = None,
				  keycloak_audience: str = None,
				  cert_path: str = None) -> Client:
	"""
	Quick connection helper for testing/development.

	Example:
		client = quick_connect()
		tools = client.list_tools()
		result = client.call_tool_simple("my_tool", {"arg": "value"})
	"""
	return Client(
		server_address=server_address,
		keycloak_url=keycloak_url,
		keycloak_realm=keycloak_realm,
		keycloak_client_id=keycloak_client_id,
		keycloak_client_secret=keycloak_client_secret,
		keycloak_audience=keycloak_audience,
		cert_path=cert_path
	)