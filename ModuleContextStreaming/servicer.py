# In ModuleContextStreaming/servicer.py
"""
Implements the gRPC servicer logic for the ModuleContextStreaming service.
"""
import json
import sys

import grpc
from google.protobuf.json_format import MessageToDict

from . import mcs_pb2, mcs_pb2_grpc


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