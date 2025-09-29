# In tests/test_server.py

import pytest
import grpc
from ModuleContextStreaming import mcs_pb2, mcs_pb2_grpc


# --- MODIFIED: Removed scope="module" ---
@pytest.fixture
def grpc_stub(grpc_server, testing_certs):
	"""Creates a gRPC client stub connected to the test server."""
	with open(testing_certs['cert_path'], 'rb') as f:
		credentials = grpc.ssl_channel_credentials(f.read())
	with grpc.secure_channel(grpc_server, credentials) as channel:
		yield mcs_pb2_grpc.ModuleContextStub(channel)


# --- Test Cases ---
# (No changes needed in the test functions themselves)

def test_list_tools(grpc_stub, mock_tool_registry):
	"""
	Verify that the ListTools RPC returns the correct tool definitions.
	"""
	request = mcs_pb2.ListToolsRequest()
	response = grpc_stub.ListTools(request)

	assert len(response.tools) == len(mock_tool_registry)
	tool_names = {tool.name for tool in response.tools}
	assert tool_names == set(mock_tool_registry.keys())

	for tool in response.tools:
		assert tool.description == mock_tool_registry[tool.name].__doc__


def test_call_tool_text_output(grpc_stub):
	"""
	Verify that calling a tool yielding text streams correct TextBlock chunks.
	"""
	request = mcs_pb2.ToolCallRequest(tool_name="text_tool")
	responses = list(grpc_stub.CallTool(request))

	assert len(responses) == 2
	assert responses[0].sequence_id == 0
	assert responses[0].WhichOneof('content_block') == 'text'
	assert responses[0].text.text == "Text response 1"

	assert responses[1].sequence_id == 1
	assert responses[1].text.text == "Text response 2"


def test_call_tool_bytes_output(grpc_stub):
	"""
	Verify that calling a tool yielding bytes streams correct ImageBlock chunks.
	"""
	request = mcs_pb2.ToolCallRequest(tool_name="bytes_tool")
	responses = list(grpc_stub.CallTool(request))

	assert len(responses) == 1
	assert responses[0].sequence_id == 0
	assert responses[0].WhichOneof('content_block') == 'image'
	assert responses[0].image.data == b"\x89PNG\r\n\x1a\n\x00\x00"
	assert responses[0].image.mime_type == "image/jpeg"


def test_call_tool_not_found(grpc_stub):
	"""
	Verify that calling a non-existent tool returns a NOT_FOUND error.
	"""
	request = mcs_pb2.ToolCallRequest(tool_name="non_existent_tool")

	with pytest.raises(grpc.RpcError) as e:
		list(grpc_stub.CallTool(request))

	assert e.value.code() == grpc.StatusCode.NOT_FOUND
	assert "Tool 'non_existent_tool' not found" in e.value.details()