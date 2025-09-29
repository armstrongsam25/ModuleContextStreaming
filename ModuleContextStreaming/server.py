# In ModuleContextStreaming/server.py
"""
Provides a reusable Server class for running a secure, authenticated gRPC service.
"""
import sys
import traceback
from concurrent import futures
import grpc
from google.protobuf.json_format import MessageToDict

from . import mcs_pb2, mcs_pb2_grpc
from .auth import KeycloakAuthenticator, AuthInterceptor


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
        for result_chunk in tool_function(arguments):
            chunk_kwargs = {'sequence_id': sequence_id}
            if isinstance(result_chunk, bytes):
                chunk_kwargs['image'] = mcs_pb2.ImageBlock(data=result_chunk, mime_type="image/jpeg")
            else:
                chunk_kwargs['text'] = mcs_pb2.TextBlock(text=str(result_chunk))
            yield mcs_pb2.ToolCallChunk(**chunk_kwargs)
            sequence_id += 1

class Server:
    """A configurable gRPC server for the ModuleContextStreaming service."""
    def __init__(self, tool_registry, port, keycloak_url, keycloak_realm, keycloak_audience, key_path, cert_path):
        """
        Initializes the Server with all necessary configuration.

        Args:
            tool_registry (dict): Maps tool names to their implementation functions.
            port (int): The port number to listen on.
            keycloak_url (str): The base URL of the Keycloak server.
            keycloak_realm (str): The Keycloak realm.
            keycloak_audience (str): The Keycloak audience for token validation.
            key_path (str): Path to the server's private key file.
            cert_path (str): Path to the server's certificate file.
        """
        self.tool_registry = tool_registry
        self.port = port
        self.keycloak_url = keycloak_url
        self.keycloak_realm = keycloak_realm
        self.keycloak_audience = keycloak_audience
        self.key_path = key_path
        self.cert_path = cert_path

    def run(self):
        """Starts the gRPC server and waits for termination."""
        try:
            authenticator = KeycloakAuthenticator(self.keycloak_url, self.keycloak_realm, self.keycloak_audience)
            auth_interceptor = AuthInterceptor(authenticator)

            with open(self.key_path, 'rb') as f:
                private_key = f.read()
            with open(self.cert_path, 'rb') as f:
                certificate_chain = f.read()

            server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
            server = grpc.server(
                futures.ThreadPoolExecutor(max_workers=10),
                interceptors=(auth_interceptor,)
            )

            servicer_instance = ModuleContextServicer(self.tool_registry)
            mcs_pb2_grpc.add_ModuleContextServicer_to_server(servicer_instance, server)

            server.add_secure_port(f'[::]:{self.port}', server_credentials)
            print(f"✅ Secure server started, listening on port {self.port}.")
            server.start()
            server.wait_for_termination()

        except FileNotFoundError as e:
            print(f"❌ Error: Certificate file not found: {e.filename}", file=sys.stderr)
        except Exception as e:
            print(f"❌ An error occurred during server startup: {e}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
