# mcs_mcp_adapter.py
"""
Adapter to connect ModuleContextStreaming gRPC service to MCP servers
"""
import asyncio
import json
from typing import AsyncIterator, Dict, Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPToolAdapter:
	"""Wraps an MCP server connection as MCS tools"""

	def __init__(self, server_params: StdioServerParameters):
		self.server_params = server_params
		self.session = None
		self.tools_cache = {}

	async def connect(self):
		"""Initialize connection to MCP server"""
		self.read, self.write = await stdio_client(self.server_params)
		self.session = ClientSession(self.read, self.write)

		await self.session.initialize()

		# Fetch available tools from MCP server
		result = await self.session.list_tools()
		self.tools_cache = {tool.name: tool for tool in result.tools}

	def get_mcs_tools(self) -> Dict[str, callable]:
		"""Convert MCP tools to MCS tool registry format"""
		registry = {}

		for tool_name, tool_def in self.tools_cache.items():
			# Create a closure that captures tool_name
			def make_tool_function(name):
				async def tool_function(arguments: Dict[str, Any]) -> AsyncIterator:
					"""Generated tool function for MCP tool"""
					result = await self.session.call_tool(name, arguments)

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

				tool_function.__doc__ = tool_def.description or f"MCP Tool: {name}"
				return tool_function

			registry[tool_name] = make_tool_function(tool_name)

		return registry