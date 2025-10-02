# In ModuleContextStreaming/mcs_mcp_adapter.py
"""
Adapter to connect the ModuleContextStreaming gRPC service to MCP backends.
This version properly handles SessionMessage serialization for the MCP protocol.
"""
import asyncio
import base64
import dataclasses
import json
import subprocess
import sys
import traceback
from typing import Any, Dict, Optional

# Conditional import for MCP library
try:
    from mcp import ClientSession, StdioServerParameters

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    ClientSession = None
    StdioServerParameters = None


def _serialize_message(obj):
    """Convert a SessionMessage or other MCP object to a JSON-serializable dict."""
    # Handle None
    if obj is None:
        return None

    # Handle basic JSON-serializable types
    if isinstance(obj, (str, int, float, bool)):
        return obj

    # Handle lists and tuples recursively
    if isinstance(obj, (list, tuple)):
        return [_serialize_message(item) for item in obj]

    # Handle dicts recursively
    if isinstance(obj, dict):
        return {k: _serialize_message(v) for k, v in obj.items()}

    # Handle Pydantic models (v2)
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')

    # Handle Pydantic models (v1)
    if hasattr(obj, 'dict'):
        return _serialize_message(obj.dict())

    # Handle dataclasses recursively
    if dataclasses.is_dataclass(obj):
        result = {}
        for field in dataclasses.fields(obj):
            value = getattr(obj, field.name)
            result[field.name] = _serialize_message(value)
        return result

    # Handle objects with __dict__
    if hasattr(obj, '__dict__'):
        return _serialize_message(obj.__dict__)

    # Fallback: convert to string
    return str(obj)


class _ProcReceiveStream:
    """Wraps a subprocess's stdout to deserialize JSON-RPC messages."""

    def __init__(self, proc_stdout, loop):
        self._stream = proc_stdout
        self._loop = loop

    async def receive(self):
        """Reads a line and parses it as JSON."""
        line_bytes = await self._loop.run_in_executor(None, self._stream.readline)
        if not line_bytes:
            raise EOFError("MCP subprocess stream has been closed.")
        return json.loads(line_bytes.decode('utf-8'))


class _ProcSendStream:
    """Wraps a subprocess's stdin to serialize messages to JSON."""

    def __init__(self, proc_stdin, loop):
        self._stream = proc_stdin
        self._loop = loop

    async def send(self, message):
        """Serializes a message to JSON bytes and writes it to the stream."""
        # Convert SessionMessage or other MCP objects to dict
        message_dict = _serialize_message(message)

        # Convert to JSON string, encode to bytes, and add newline
        json_bytes = json.dumps(message_dict).encode('utf-8') + b'\n'
        await self._loop.run_in_executor(None, lambda: self._stream.write(json_bytes))
        await self._loop.run_in_executor(None, lambda: self._stream.flush())


class MCPToolAdapter:
    """Wraps an MCP server connection and exposes its tools in MCS format."""

    def __init__(self, name: str, server_params: 'StdioServerParameters'):
        if not MCP_AVAILABLE:
            raise ImportError("MCP library is required. Install with: pip install mcp")
        self.name = name
        self.server_params = server_params
        self.session: Optional['ClientSession'] = None
        self.tools_cache: Dict[str, Any] = {}
        self._proc: Optional[subprocess.Popen] = None

    async def connect(self):
        """Initialize connection using subprocess.Popen and stream wrapper classes."""
        print(f"🔌 Connecting to MCP backend: {self.name}")
        loop = asyncio.get_running_loop()

        try:
            self._proc = await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    [self.server_params.command] + self.server_params.args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=sys.stderr,
                    env=self.server_params.env
                )
            )

            # Create stream objects with the correct interface
            read_stream = _ProcReceiveStream(self._proc.stdout, loop)
            write_stream = _ProcSendStream(self._proc.stdin, loop)

            self.session = ClientSession(read_stream, write_stream)
            await self.session.initialize()

            result = await self.session.list_tools()
            self.tools_cache = {tool.name: tool for tool in result.tools}
            print(f"✅ Connected to MCP backend '{self.name}' with {len(self.tools_cache)} tools")

        except Exception as e:
            print(f"❌ Failed to connect to MCP backend '{self.name}': {e}", file=sys.stderr)
            traceback.print_exc()
            await self.close()
            raise

    def get_mcs_tools(self) -> Dict[str, callable]:
        """Convert MCP tools to the MCS tool registry format."""
        registry = {}
        for tool_name, tool_def in self.tools_cache.items():
            prefixed_name = f"{self.name}:{tool_name}"

            def make_tool_function(name: str, t_def, backend_name: str):
                async def tool_function(arguments: Dict[str, Any]):
                    """Generated async tool function for an MCP tool."""
                    try:
                        result = await self.session.call_tool(name, arguments)
                        for content in result.content:
                            if content.type == "text":
                                yield content.text
                            elif content.type == "image":
                                yield base64.b64decode(content.data)
                            elif content.type == "resource":
                                if hasattr(content.resource, 'text'):
                                    yield f"[Resource: {content.resource.uri}]\n{content.resource.text}"
                                elif hasattr(content.resource, 'blob'):
                                    yield base64.b64decode(content.resource.blob)
                        if hasattr(result, 'structuredContent') and result.structuredContent:
                            yield f"\n[Structured Output]\n{json.dumps(result.structuredContent, indent=2)}"
                        if hasattr(result, 'isError') and result.isError:
                            yield f"\n[Error: Tool execution failed]"
                    except Exception as e:
                        yield f"[MCP Error: {str(e)}]"

                def sync_tool_function_wrapper(arguments: Dict[str, Any]):
                    loop = asyncio.get_event_loop_policy().new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        async_gen = tool_function(arguments)
                        while True:
                            try:
                                yield loop.run_until_complete(async_gen.__anext__())
                            except StopAsyncIteration:
                                break
                    finally:
                        loop.close()

                sync_tool_function_wrapper.__doc__ = t_def.description or f"MCP Tool from '{backend_name}': {name}"
                return sync_tool_function_wrapper

            registry[prefixed_name] = make_tool_function(tool_name, tool_def, self.name)
        return registry

    async def close(self):
        """Close the MCP session and terminate the subprocess."""
        loop = asyncio.get_running_loop()
        try:
            if self.session:
                # Check if session has close method
                if hasattr(self.session, '__aexit__'):
                    await self.session.__aexit__(None, None, None)
                elif hasattr(self.session, 'close'):
                    await self.session.close()
            if self._proc and self._proc.poll() is None:
                print(f"🔌 Terminating MCP backend subprocess: {self.name}")
                await loop.run_in_executor(None, lambda: self._proc.terminate())
                try:
                    await loop.run_in_executor(None, lambda: self._proc.wait(timeout=5))
                except:
                    await loop.run_in_executor(None, lambda: self._proc.kill())
        except Exception as e:
            print(f"⚠️  Error closing MCP connection '{self.name}': {e}", file=sys.stderr)
            if self._proc and self._proc.poll() is None:
                await loop.run_in_executor(None, lambda: self._proc.kill())