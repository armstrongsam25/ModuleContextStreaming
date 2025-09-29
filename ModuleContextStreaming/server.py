# In ModuleContextStreaming/server.py
"""
Provides the core scaffolding for running a secure, authenticated gRPC server.

This module is tool-agnostic. It expects a registry of tools to be
injected into the serve() function upon startup.
"""
import argparse
import os
import traceback
from concurrent import futures

import grpc
from dotenv import load_dotenv
from google.protobuf.json_format import MessageToDict

from . import mcs_pb2, mcs_pb2_grpc
from .auth import KeycloakAuthenticator, AuthInterceptor

class ModuleContextServicer(mcs_pb2_grpc.ModuleContextServicer):
    """
    Provides generic implementations for the gRPC service methods.
    It uses a tool registry provided during initialization.
    """

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
            print(f"❌ An unexpected error occurred in ListTools: {e}")
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


def serve(tool_registry):
    """
    Configures and runs the secure gRPC server with a given set of tools.

    Args:
        tool_registry (dict): A dictionary mapping tool names to their
                              callable implementation functions.
    """
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the MCS gRPC server.")
    parser.add_argument('--port', default=os.getenv('MCS_PORT', '50051'))
    parser.add_argument('--keycloak-url', default=os.getenv('KEYCLOAK_URL'))
    parser.add_argument('--keycloak-realm', default=os.getenv('KEYCLOAK_REALM'))
    parser.add_argument('--keycloak-audience', default=os.getenv('KEYCLOAK_AUDIENCE'))
    args = parser.parse_args()

    if not all([args.keycloak_url, args.keycloak_realm, args.keycloak_audience]):
        print("❌ Error: Keycloak settings must be provided.")
        return

    try:
        authenticator = KeycloakAuthenticator(args.keycloak_url, args.keycloak_realm, args.keycloak_audience)
        auth_interceptor = AuthInterceptor(authenticator)

        with open('certs/private.key', 'rb') as f:
            private_key = f.read()
        with open('certs/certificate.pem', 'rb') as f:
            certificate_chain = f.read()

        server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))

        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=10),
            interceptors=(auth_interceptor,)
        )

        # The servicer is now initialized with the tool registry
        servicer_instance = ModuleContextServicer(tool_registry)
        mcs_pb2_grpc.add_ModuleContextServicer_to_server(servicer_instance, server)

        server.add_secure_port(f'[::]:{args.port}', server_credentials)
        print(f"✅ Secure server started, listening on port {args.port}.")
        server.start()
        server.wait_for_termination()

    except FileNotFoundError:
        print("❌ Error: Certificate files (private.key, certificate.pem) not found in 'certs/' directory.")
    except Exception as e:
        print(f"❌ An error occurred during server startup: {e}")
        print(traceback.format_exc())
