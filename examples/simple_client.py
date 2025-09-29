# In examples/simple_client.py
"""
An example runnable gRPC client that imports and uses the core client library.
"""
from ModuleContextStreaming.client import setup_client, call_tool
from ModuleContextStreaming import mcs_pb2


def main():
    """Sets up the client and runs a sequence of example tool calls."""
    stub, auth_metadata, channel = setup_client()

    if not stub:
        print("Client setup failed. Exiting.")
        return

    # Use a try/finally block to ensure the channel is closed
    try:
        print("\n----- Listing Available Tools -----")
        try:
            list_tools_request = mcs_pb2.ListToolsRequest()
            list_tools_response = stub.ListTools(list_tools_request, metadata=auth_metadata)
            print("✅ Tools available from server:")
            for tool in list_tools_response.tools:
                print(f"  - {tool.name}: {tool.description}")
        except Exception as e:
            print(f"❌ Error listing tools: {e}")

        example_calls = [
            {
                "tool_name": "render_html",
                "args": {"html_string": "<h1>Hello again!</h1>"}
            },
            {
                "tool_name": "web_search",
                "args": {"query": "gRPC Python Interceptors"}
            },
            {
                "tool_name": "image_fetcher",
                "args": {"url": "https://www.python.org/static/community_logos/python-logo-master-v3-TM.png"}
            }
        ]

        for call in example_calls:
            call_tool(stub, auth_metadata, call['tool_name'], call['args'])

    finally:
        if channel:
            channel.close()
            print("\nConnection closed.")


if __name__ == '__main__':
    main()