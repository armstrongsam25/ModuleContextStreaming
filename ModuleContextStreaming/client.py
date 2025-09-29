# In ModuleContextStreaming/client.py
"""
Provides a reusable Client class for connecting to the ModuleContextStreaming service.
"""
import sys

import grpc
import requests
from google.protobuf.json_format import ParseDict

from . import mcs_pb2, mcs_pb2_grpc


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

        print("🚀 Initializing MCS Client...")
        print("🔑 Authenticating with Keycloak...")
        jwt_token = self._get_keycloak_token(keycloak_url, keycloak_realm, keycloak_client_id, keycloak_client_secret,
                                             keycloak_audience)
        if not jwt_token:
            raise ConnectionError("Failed to authenticate with Keycloak.")
        print("✅ Successfully authenticated.")
        self.auth_metadata = [('authorization', f'Bearer {jwt_token}')]

        if cert_path:
            # Secure Mode
            print(f" HINT: Using custom certificate for secure connection: {cert_path}")
            try:
                with open(cert_path, 'rb') as f:
                    trusted_certs = f.read()
                credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
                self.channel = grpc.secure_channel(self.server_address, credentials)
            except FileNotFoundError:
                raise FileNotFoundError(f"Certificate file not found at '{cert_path}'.")
        else:
            # Insecure Mode
            print("⚠️ WARNING: Client connecting via an INSECURE channel. Do not use in production.")
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

    def list_tools(self):
        """
        Requests the list of available tools from the server.

        Returns:
            list: A list of ToolDefinition protobuf messages, or an empty list on error.
        """
        try:
            print("\n----- Listing Available Tools -----")
            request = mcs_pb2.ListToolsRequest()
            response = self.stub.ListTools(request, metadata=self.auth_metadata)
            print(f"✅ Found {len(response.tools)} tools available from server:")
            return response.tools
        except grpc.RpcError as e:
            print(f"❌ Error listing tools: {e.code().name}: {e.details()}", file=sys.stderr)
            return []

    def call_tool(self, tool_name, arguments_dict):
        """
        Performs a tool call and yields the streamed response chunks.

        Args:
            tool_name (str): The name of the tool to execute.
            arguments_dict (dict): A dictionary of arguments for the tool.

        Yields:
            ToolCallChunk: A protobuf message for each chunk of the response.
        """
        try:
            print(f"\n----- Calling Tool: {tool_name} with args: {arguments_dict} -----")
            arguments_struct = mcs_pb2.google_dot_protobuf_dot_struct__pb2.Struct()
            ParseDict(arguments_dict, arguments_struct)
            stream_request = mcs_pb2.ToolCallRequest(tool_name=tool_name, arguments=arguments_struct)

            yield from self.stub.CallTool(stream_request, metadata=self.auth_metadata)
            print("✅ Stream finished.")
        except grpc.RpcError as e:
            print(f"❌ Error during CallTool ({tool_name}): {e.code().name}: {e.details()}", file=sys.stderr)

    def close(self):
        """Closes the gRPC channel."""
        if self.channel:
            self.channel.close()
            print("🔌 Client connection closed.")
