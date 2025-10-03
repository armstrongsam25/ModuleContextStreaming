# In ModuleContextStreaming/client.py
"""
Provides a reusable Client class for connecting to the ModuleContextStreaming service.
Enhanced with MCP-aware content parsing and handling.
"""
import json
import os
import sys
from typing import Dict, List, Any, Optional, Iterator, Callable

import grpc
import requests
from google.protobuf.json_format import ParseDict

import time
from jose import jwt


try:
	import openai
except ImportError:
	raise ImportError(
		"The 'openai' package is required for the LLM chat feature. "
		"Please install it using 'pip install openai'."
	)

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
		self.keycloak_url = keycloak_url
		self.keycloak_realm = keycloak_realm
		self.keycloak_client_id = keycloak_client_id
		self.keycloak_client_secret = keycloak_client_secret
		self.keycloak_audience = keycloak_audience

		self.auth_metadata = None
		self.channel = None
		self.stub = None
		self.parser = MCPContentParser()

		self.access_token: Optional[str] = None
		self.refresh_token: Optional[str] = None
		self.token_endpoint: Optional[str] = None
		self.token_expires_at: int = 0

		print("🚀 Initializing MCS Client...")
		self._authenticate()

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

	def _update_token_state(self, token_data: dict):
		"""
		Decodes a new access token to store it along with its expiration time.
		"""
		self.access_token = token_data['access_token']
		# Update refresh token if a new one is provided
		self.refresh_token = token_data.get('refresh_token', self.refresh_token)

		try:
			# Decode the token without verifying the signature to quickly get claims
			claims = jwt.decode(
				self.access_token,
				key=None,  # No key needed when not verifying signature
				options={"verify_signature": False, "verify_aud": False, "verify_exp": False}
			)
			self.token_expires_at = claims.get('exp', 0)
		except Exception:
			# If decoding fails, set expiration to 0 to force a refresh on the next call
			self.token_expires_at = 0

	def _authenticate(self):
		"""Fetches and stores access and refresh tokens from Keycloak."""
		print("🔐 Authenticating with Keycloak...")
		try:
			# Discover the token endpoint from OIDC config for robustness
			oidc_config_url = f"{self.keycloak_url}/realms/{self.keycloak_realm}/.well-known/openid-configuration"
			oidc_config = requests.get(oidc_config_url, timeout=5).json()
			self.token_endpoint = oidc_config['token_endpoint']

			response = requests.post(
				self.token_endpoint,
				data={
					"grant_type": "client_credentials",
					"client_id": self.keycloak_client_id,
					"client_secret": self.keycloak_client_secret,
					"audience": self.keycloak_audience,
				},
				timeout=10
			)
			response.raise_for_status()

			self._update_token_state(response.json())
			print("✅ Successfully authenticated.")
		except requests.exceptions.RequestException as e:
			print(f"❌ Authentication failed: {e}", file=sys.stderr)
			raise ConnectionError(f"Failed to authenticate with Keycloak: {e}")

	def _refresh_token(self):
		"""Uses the refresh token to get a new access token."""
		if not self.refresh_token:
			print("⚠️ No refresh token available. Performing full re-authentication.")
			self._authenticate()
			return

		print("🔄 Token expired. Refreshing...")
		try:
			response = requests.post(
				self.token_endpoint,
				data={
					"grant_type": "refresh_token",
					"refresh_token": self.refresh_token,
					"client_id": self.keycloak_client_id,
					"client_secret": self.keycloak_client_secret,
				},
				timeout=10
			)
			response.raise_for_status()

			self._update_token_state(response.json())
			print("✅ Token refreshed successfully.")
		except requests.exceptions.RequestException as e:
			print(f"❌ Failed to refresh token: {e}. A full re-authentication will be required.", file=sys.stderr)
			self.access_token = None  # Invalidate token to force re-auth
			raise ConnectionError("Token refresh failed.")

	def _make_grpc_call(self, call_func: Callable, request: Any) -> Any:
		"""
		A wrapper for gRPC calls that proactively refreshes the token and
		includes a reactive fallback for retries.
		"""
		# --- Proactive Refresh ---
		# Check if the token exists and is within 30 seconds of expiring
		if self.access_token and time.time() > (self.token_expires_at - 30):
			print("ℹ️ Token is about to expire. Proactively refreshing...")
			try:
				self._refresh_token()
			except ConnectionError:
				# If proactive refresh fails, try a full re-authentication
				self._authenticate()

		# --- Reactive Fallback (for edge cases) ---
		try:
			# First attempt
			if not self.access_token: self._authenticate()
			auth_metadata = [('authorization', f'Bearer {self.access_token}')]
			return call_func(request, metadata=auth_metadata)
		except grpc.RpcError as e:
			# If the first attempt fails with UNAUTHENTICATED, refresh and retry once
			if e.code() == grpc.StatusCode.UNAUTHENTICATED:
				print("⚠️ Received UNAUTHENTICATED despite proactive checks. Reactively refreshing...")
				try:
					self._refresh_token()
					# Second attempt
					auth_metadata = [('authorization', f'Bearer {self.access_token}')]
					return call_func(request, metadata=auth_metadata)
				except (ConnectionError, grpc.RpcError) as final_e:
					# If the refresh or the second attempt fails, raise the final error
					raise final_e
			# For any other initial error, raise it immediately
			raise e

	def start_llm_chat_session(
			self,
			llm_api_key: str,
			model_name: Optional[str] = None,
			base_url: Optional[str] = None
	) -> 'LLMToolChat':
		"""
		Initializes and returns an LLMToolChat session connected to this client.

		This is the primary entry point for using the LLM integration.

		Args:
			llm_api_key (str): Your OpenAI-compatible API key.
			model_name (str, optional): The name of the model to use (e.g., 'gpt-4o').
										If None, it will try to read from the LLM_MODEL env var.
			base_url (str, optional): The base URL for the LLM API.
									  If None, it will try to read from the LLM_API_URL env var.

		Returns:
			LLMToolChat: An interactive chat session instance.
		"""

		return LLMToolChat(
			mcs_client=self,
			llm_api_key=llm_api_key,
			model_name=model_name,
			base_url=base_url
		)


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
			response = self._make_grpc_call(self.stub.ListTools, request)

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

			response_iterator = self._make_grpc_call(self.stub.CallTool, stream_request)

			for chunk in response_iterator:
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


class LLMToolChat:
	"""Interactive chat session with an LLM that can use MCS tools."""

	def __init__(self, mcs_client: Client, llm_api_key: str, model_name: Optional[str], base_url: Optional[str]):
		self.mcs_client = mcs_client
		self.conversation_history = []

		self.model = model_name or os.environ.get('LLM_MODEL')
		api_base_url = base_url or os.environ.get('LLM_API_URL')

		self.llm = openai.OpenAI(api_key=llm_api_key, base_url=api_base_url)
		self.tool_name_map = {}

	def _summarize_text(self, text_to_summarize: str, topic: str) -> str:
		"""
		Uses the LLM to summarize a long block of text.
		"""
		# Heuristic: if text is reasonably short, don't waste an API call
		if len(text_to_summarize) < 3000:
			return text_to_summarize

		print(f"📝 Text is too long ({len(text_to_summarize)} chars). Summarizing...")

		# A simple, focused prompt for the summarization task
		summary_prompt = (
			f"Please provide a concise summary of the following text, "
			f"focusing on key facts and biographical information relevant to '{topic}'.\n\n"
			f"TEXT:\n\"\"\"\n{text_to_summarize}\n\"\"\""
		)

		try:
			# Make a separate, non-tool-using API call for the summary
			response = self.llm.chat.completions.create(
				model=self.model,
				messages=[{"role": "user", "content": summary_prompt}],
				# No tools are needed for this simple task
			)
			summary = response.choices[0].message.content
			print("✅ Summarization complete.")
			return summary or "No summary could be generated."
		except Exception as e:
			print(f"❌ Error during summarization: {e}")
			# Fallback: return a truncated version of the text if summarization fails
			return text_to_summarize[:3000] + "\n... (text truncated)"


	def _format_tools_for_llm(self):
		"""Convert MCS tools to LLM tool format."""
		mcs_tools = self.mcs_client.list_tools(verbose=False)
		self.tool_name_map.clear()  # Clear map on each format
		llm_tools = []

		for tool in mcs_tools:
			# LLMs often struggle with ":" in function names, so we replace it
			llm_tool_name = tool.name.replace(":", "_")

			# Store the mapping from the LLM-safe name back to the original
			self.tool_name_map[llm_tool_name] = tool.name

			llm_tools.append({
				"type": "function",
				"function": {
					"name": llm_tool_name,  # Use the safe name
					"description": tool.description,
					"parameters": {
						"type": "object",
						"properties": {
							"arguments": {"type": "object", "description": "Tool arguments"}
						}
					}
				}
			})
		return llm_tools

	def _call_mcs_tool(self, tool_name: str, arguments: dict):
		"""Execute an MCS tool and return results."""

		original_name = self.tool_name_map.get(tool_name)

		if not original_name:
			# This handles cases where the LLM hallucinates a tool name
			return f"Error: The LLM tried to call a non-existent tool: '{tool_name}'."

		print(f"\n🔧 Executing tool: {original_name}")
		print(f"   Arguments: {arguments}")

		result = self.mcs_client.call_tool_parsed(original_name, arguments, verbose=False)

		# Combine all content into a response
		response_parts = []
		if result['text']:
			response_parts.append('\n'.join(result['text']))
		if result['errors']:
			response_parts.append(f"Errors: {'; '.join(result['errors'])}")
		if result['structured']:
			response_parts.append(f"Structured data: {json.dumps(result['structured'], indent=2)}")

		return '\n'.join(response_parts) if response_parts else "Tool executed successfully (no output)"

	def _chat_openai(self, user_message: str):
		"""Handle chat with OpenAI's GPT."""
		self.conversation_history.append({
			"role": "user",
			"content": user_message
		})

		tools = self._format_tools_for_llm()

		while True:
			response = self.llm.chat.completions.create(
				model=self.model,
				messages=self.conversation_history,
				tools=tools
			)

			message = response.choices[0].message

			# Check if GPT wants to use tools
			if message.tool_calls:
				# Add assistant's response to history
				self.conversation_history.append({
					"role": "assistant",
					"content": message.content,
					"tool_calls": message.tool_calls
				})

				# Execute all tool calls
				for tool_call in message.tool_calls:
					tool_name = tool_call.function.name
					tool_args = json.loads(tool_call.function.arguments).get("arguments", {})

					# Get the raw result from the tool
					raw_result = self._call_mcs_tool(tool_name, tool_args)

					final_result = raw_result
					# If the tool was web_fetcher, summarize its output before proceeding
					if tool_name == "web_fetcher":
						# We need a topic for the summary. We can extract it from the initial user message.
						# This is a simple heuristic; more advanced methods could be used.
						topic = user_message.split("web for")[-1].split("and create")[0].strip()
						final_result = self._summarize_text(raw_result, topic)

					self.conversation_history.append({
						"role": "tool",
						"tool_call_id": tool_call.id,
						"content": final_result  # Use the (potentially summarized) final result
					})
				continue
			else:
				# No more tools needed
				self.conversation_history.append({
					"role": "assistant",
					"content": message.content
				})

				return message.content

	def chat(self, user_message: str) -> str:
		"""Send a message and get a response."""
		return self._chat_openai(user_message)

	def run_interactive(self):
		"""Run an interactive chat loop."""
		print("\n" + "=" * 60)
		print("Interactive LLM Chat with MCS Tools")
		print("=" * 60)
		print("\nThe LLM has access to these tools:")

		tools = self.mcs_client.list_tools(verbose=False)
		for tool in tools:
			print(f"  - {tool.name}")

		print("\nType your message and press Enter. Type 'quit' to exit.")
		print("=" * 60 + "\n")

		while True:
			try:
				user_input = input("\nYou: ").strip()

				if user_input.lower() in ['quit', 'exit', 'q']:
					print("Goodbye!")
					break

				if not user_input:
					continue

				print("\n🤖 Assistant: ", end="", flush=True)
				response = self.chat(user_input)
				print(response)

			except KeyboardInterrupt:
				print("\n\nGoodbye!")
				break
			except Exception as e:
				print(f"\n❌ Error: {e}")
				import traceback
				traceback.print_exc()
