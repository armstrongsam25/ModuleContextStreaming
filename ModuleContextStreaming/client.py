# In ModuleContextStreaming/client.py

import grpc
import os
import argparse
import requests
from dotenv import load_dotenv

# Import the generated classes
from . import mcs_pb2
from . import mcs_pb2_grpc


def get_keycloak_token(url, realm, client_id, client_secret, audience=None):
    """Fetches an access token from Keycloak using Client Credentials Flow."""
    token_url = f"{url}/realms/{realm}/protocol/openid-connect/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    # Add audience to the request if provided
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
    """Main function to run the gRPC client."""
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run the MCS gRPC client.")
    parser.add_argument('--server-address', default=os.getenv('MCS_SERVER_ADDRESS', 'localhost:50051'))
    parser.add_argument('--keycloak-url', default=os.getenv('KEYCLOAK_URL'))
    parser.add_argument('--keycloak-realm', default=os.getenv('KEYCLOAK_REALM'))
    parser.add_argument('--keycloak-client-id', default=os.getenv('KEYCLOAK_CLIENT_ID'))
    parser.add_argument('--keycloak-audience', default=os.getenv('KEYCLOAK_AUDIENCE'))
    parser.add_argument('--keycloak-client-secret', default=os.getenv('KEYCLOAK_CLIENT_SECRET'))
    args = parser.parse_args()

    if not all([args.keycloak_url, args.keycloak_realm, args.keycloak_client_id, args.keycloak_audience, args.keycloak_client_secret]):
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
        return  # Stop if authentication failed


    print("\n--- DEBUG: YOUR TOKEN ---")
    print(jwt_token)  # <--- ADD THIS LINE
    print("--- END DEBUG ---\n")

    print("✅ Successfully authenticated.")
    auth_metadata = [('authorization', f'Bearer {jwt_token}')]

    with grpc.insecure_channel(args.server_address) as channel:
        stub = mcs_pb2_grpc.ModuleContextStub(channel)

        # --- 1. Call the ListTools method (protected method) ---
        print("\n----- Calling ListTools -----")
        try:
            list_tools_request = mcs_pb2.ListToolsRequest()
            print(auth_metadata)

            print(list_tools_request)
            list_tools_response = stub.ListTools(list_tools_request, metadata=auth_metadata) # TODO: problem here
            print("✅ Available tools from server:")
            for tool in list_tools_response.tools:
                print(f"  - {tool.name}: {tool.description}")
        except grpc.RpcError as e:
            print(f"❌ A gRPC error occurred in StreamToolCall: {e.code().name} ({e.details()})")

        # --- 2. Call the StreamToolCall method (protected method) ---
        print("\n----- Calling StreamToolCall -----")
        try:
            stream_request = mcs_pb2.ToolCallRequest(tool_name="file_reader")
            print(f"✅ Streaming response for '{stream_request.tool_name}':")
            for chunk in stub.StreamToolCall(stream_request, metadata=auth_metadata):
                print(f"  [Chunk {chunk.sequence_id}] Received: {chunk.content_chunk.strip()}")
            print("✅ Stream finished.")
        except grpc.RpcError as e:
            print(f"❌ Error during StreamToolCall: {e.code().name}: {e.details()}")


if __name__ == '__main__':
    run()