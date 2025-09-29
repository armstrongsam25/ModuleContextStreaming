# In ModuleContextStreaming/client.py
"""
A secure, authenticated gRPC client for the ModuleContextStreaming service.

This script handles authenticating with a Keycloak server to obtain a JWT,
then uses that token to make secure gRPC calls over a TLS-encrypted channel.
It demonstrates calling various tools available on the server, including
unary and streaming RPCs, and handles different types of streamed content
like text, HTML, and binary image data.
"""
import argparse
import os

import grpc
import requests
from dotenv import load_dotenv
from google.protobuf.json_format import ParseDict

from . import mcs_pb2
from . import mcs_pb2_grpc


def get_keycloak_token(url, realm, client_id, client_secret, audience=None):
    """
    Fetches an access token from Keycloak using the Client Credentials Flow.

    Args:
        url (str): The base URL of the Keycloak server.
        realm (str): The Keycloak realm name.
        client_id (str): The client ID to authenticate with.
        client_secret (str): The client secret for the specified client.
        audience (str, optional): The audience for the token. Defaults to None.

    Returns:
        str: The JWT access token on success.
        None: If authentication fails or a network error occurs.
    """
    token_url = f"{url}/realms/{realm}/protocol/openid-connect/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if audience:
        payload["audience"] = audience

    try:
        response = requests.post(token_url, data=payload, timeout=5)
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.exceptions.RequestException as e:
        print(f"❌ Could not get token from Keycloak: {e}")
        return None


def run():
    """
    The main entry point for the gRPC client.

    This function performs the following steps:
    1. Loads configuration from environment variables and command-line arguments.
    2. Authenticates with Keycloak to get a JWT access token.
    3. Establishes a secure, TLS-encrypted gRPC channel to the server.
    4. Creates a client stub.
    5. Calls the `ListTools` RPC to see available tools.
    6. Calls several `CallTool` RPCs to demonstrate handling different
       tools and response types (text, images, client-to-server HTML).
    """
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run the MCS gRPC client.")
    parser.add_argument('--server-address', default=os.getenv('MCS_SERVER_ADDRESS', 'localhost:50051'))
    parser.add_argument('--keycloak-url', default=os.getenv('KEYCLOAK_URL'))
    parser.add_argument('--keycloak-realm', default=os.getenv('KEYCLOAK_REALM'))
    parser.add_argument('--keycloak-client-id', default=os.getenv('KEYCLOAK_CLIENT_ID'))
    parser.add_argument('--keycloak-audience', default=os.getenv('KEYCLOAK_AUDIENCE'))
    parser.add_argument('--keycloak-client-secret', default=os.getenv('KEYCLOAK_CLIENT_SECRET'))
    args = parser.parse_args()

    if not all(
            [args.keycloak_url, args.keycloak_realm, args.keycloak_client_id, args.keycloak_audience,
             args.keycloak_client_secret]):
        print("❌ Error: Keycloak settings must be provided via .env, env vars, or args.")
        return

    print("🚀 Starting MCS Client...")
    print("🔑 Authenticating with Keycloak...")
    jwt_token = get_keycloak_token(
        args.keycloak_url,
        args.keycloak_realm,
        args.keycloak_client_id,
        args.keycloak_client_secret,
        audience=args.keycloak_audience
    )

    if not jwt_token:
        return

    print("✅ Successfully authenticated.")
    auth_metadata = [('authorization', f'Bearer {jwt_token}')]

    try:
        with open('certs/certificate.pem', 'rb') as f:
            trusted_certs = f.read()
    except FileNotFoundError:
        print("❌ Error: Certificate file not found at 'certs/certificate.pem'.")
        print("Please run the certificate generation script first.")
        return

    credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)

    with grpc.secure_channel(args.server_address, credentials) as channel:
        stub = mcs_pb2_grpc.ModuleContextStub(channel)

        print("\n----- Calling ListTools -----")
        try:
            list_tools_request = mcs_pb2.ListToolsRequest()
            list_tools_response = stub.ListTools(list_tools_request, metadata=auth_metadata)
            print("✅ Available tools from server:")
            for tool in list_tools_response.tools:
                print(f"  - {tool.name}: {tool.description}")
        except grpc.RpcError as e:
            print(f"❌ A gRPC error occurred in ListTools: {e.code().name} ({e.details()})")

        print("\n----- Calling Tool: render_html -----")
        try:
            tool_name_to_call = "render_html"
            sample_html = """
            <!DOCTYPE html>
            <html>
            <head><title>Hello from Client</title></head>
            <body>
                <h1>This is a test</h1>
                <p>Sending this HTML to the server.</p>
            </body>
            </html>
            """
            arguments_dict = {"html_string": sample_html}
            arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(arguments_dict, arguments_struct)
            stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name_to_call, arguments=arguments_struct)

            print("✅ Sending HTML snippet to the server...")
            for chunk in stub.CallTool(stream_request, metadata=auth_metadata):
                content_type = chunk.WhichOneof('content_block')
                if content_type == 'text':
                    print(f"  [Server Response] {chunk.text.text.strip()}")
            print("✅ Finished sending HTML.")
        except grpc.RpcError as e:
            print(f"❌ Error during CallTool (render_html): {e.code().name}: {e.details()}")

        print("\n----- Calling Tool: web_search -----")
        try:
            tool_name_to_call = "web_search"
            arguments_dict = {"query": "What is gRPC?"}
            arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(arguments_dict, arguments_struct)
            stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name_to_call, arguments=arguments_struct)

            print(f"✅ Streaming response for query '{arguments_dict['query']}':")
            for chunk in stub.CallTool(stream_request, metadata=auth_metadata):
                content_type = chunk.WhichOneof('content_block')
                if content_type == 'text':
                    print(f"  {chunk.text.text.strip()}")
            print("✅ Stream finished.")
        except grpc.RpcError as e:
            print(f"❌ Error during CallTool (web_search): {e.code().name}: {e.details()}")

        print("\n----- Calling Tool: image_fetcher -----")
        try:
            tool_name_to_call = "image_fetcher"
            image_url = "https://www.grpc.io/img/logos/grpc-icon-color.png"
            arguments_dict = {"url": image_url}
            arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(arguments_dict, arguments_struct)
            stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name_to_call, arguments=arguments_struct)
            output_filename = "downloaded_image.png"

            print(f"✅ Fetching image from '{image_url}' and saving to '{output_filename}':")
            for chunk in stub.CallTool(stream_request, metadata=auth_metadata):
                content_type = chunk.WhichOneof('content_block')
                if content_type == 'image':
                    with open(output_filename, 'wb') as f:
                        f.write(chunk.image.data)
                    print(f"  [Image] Saved {len(chunk.image.data)} bytes to {output_filename}")
                elif content_type == 'text':
                    print(f"  [Error] {chunk.text.text.strip()}")
            print("✅ Image fetch finished.")
        except grpc.RpcError as e:
            print(f"❌ Error during CallTool (image_fetcher): {e.code().name}: {e.details()}")


if __name__ == '__main__':
    run()