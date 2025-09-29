# In ModuleContextStreaming/client.py
"""
Provides the core scaffolding for creating a secure, authenticated gRPC client.

This module offers functions to set up a connection and to call individual tools,
but does not contain a runnable main script itself.
"""
import argparse
import os
import uuid

import grpc
import requests
from dotenv import load_dotenv
from google.protobuf.json_format import ParseDict

from . import mcs_pb2, mcs_pb2_grpc


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

def setup_client():
    """
    Handles all client setup, including auth and creating a secure channel.

    Returns:
        tuple: A tuple containing the gRPC client stub and authentication metadata,
               or (None, None) if setup fails.
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
        print("❌ Error: Keycloak settings must be provided.")
        return None, None

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
        return None, None

    print("✅ Successfully authenticated.")
    auth_metadata = [('authorization', f'Bearer {jwt_token}')]

    try:
        with open('certs/certificate.pem', 'rb') as f:
            trusted_certs = f.read()
    except FileNotFoundError:
        print("❌ Error: Certificate file not found at 'certs/certificate.pem'.")
        return None, None

    credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
    channel = grpc.secure_channel(args.server_address, credentials)
    stub = mcs_pb2_grpc.ModuleContextStub(channel)

    return stub, auth_metadata, channel


def call_tool(stub, auth_metadata, tool_name, arguments_dict):
    """
    Performs a single tool call and handles printing the streamed response.

    Args:
        stub: The active gRPC client stub.
        auth_metadata (list): The authentication metadata for the call.
        tool_name (str): The name of the tool to execute.
        arguments_dict (dict): A dictionary of arguments for the tool.
    """
    try:
        print(f"\n----- Calling Tool: {tool_name} -----")
        print(f"✅ Arguments: {arguments_dict}")

        arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
        ParseDict(arguments_dict, arguments_struct)
        stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name, arguments=arguments_struct)
        output_filename = f"{uuid.uuid4()}.png"

        for chunk in stub.CallTool(stream_request, metadata=auth_metadata):
            content_type = chunk.WhichOneof('content_block')
            if content_type == 'text':
                print(f"  [Server Response] {chunk.text.text.strip()}")
            elif content_type == 'image':
                with open(output_filename, 'wb') as f:
                    f.write(chunk.image.data)
                print(f"  [Image] Saved {len(chunk.image.data)} bytes to {output_filename}")
        print("✅ Stream finished.")
    except grpc.RpcError as e:
        print(f"❌ Error during CallTool ({tool_name}): {e.code().name}: {e.details()}")
