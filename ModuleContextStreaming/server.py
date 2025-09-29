# In ModuleContextStreaming/server.py
"""
A secure, authenticated gRPC server for the ModuleContextStreaming service.

This server implements the MCS protocol, providing a set of tools that can be
called by a client. It features dynamic tool dispatching, JWT-based authentication
via a gRPC interceptor, and uses TLS for secure communication.
"""
import argparse
import os
import traceback
from concurrent import futures

import grpc
import requests
from dotenv import load_dotenv
from google.protobuf.json_format import MessageToDict

from . import mcs_pb2
from . import mcs_pb2_grpc
from .auth import KeycloakAuthenticator, AuthInterceptor

def tool_file_reader(arguments):
    """
    Reads a file from the local filesystem and streams its content line by line.

    Args:
        arguments (dict): A dictionary containing tool parameters.
                          Expects a 'path' key with the file path.

    Yields:
        str: Lines from the specified file or an error message.
    """
    file_path = arguments.get('path', 'README.md')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                yield f"LINE: {line.strip()}"
    except FileNotFoundError:
        yield f"ERROR: File not found at '{file_path}'."
    except UnicodeDecodeError:
        yield f"ERROR: The file at '{file_path}' is not valid UTF-8 text."


def tool_web_search(arguments):
    """
    Performs a web search using the DuckDuckGo API and streams the results.

    Args:
        arguments (dict): A dictionary containing tool parameters.
                          Expects a 'query' key with the search term.

    Yields:
        str: The search results or an error message.
    """
    query = arguments.get('query')
    if not query:
        yield "ERROR: A 'query' argument is required for web search."
        return

    yield f"Searching the web for: '{query}'..."
    try:
        # Use the DuckDuckGo Instant Answer API (no key required)
        url = f"https://api.duckduckgo.com/?q={query}&format=json"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data.get("AbstractText"):
            yield f"\nInstant Answer: {data['AbstractText']}"

        if data.get("RelatedTopics"):
            yield "\nRelated Topics:"
            for topic in data["RelatedTopics"]:
                if "Text" in topic and "FirstURL" in topic:
                    yield f"- {topic['Text']}: {topic['FirstURL']}"
        else:
            yield "No direct results found."

    except requests.RequestException as e:
        yield f"ERROR: Failed to perform web search. {e}"


def tool_image_fetcher(arguments):
    """
    Fetches an image from a URL and yields its binary content.

    Args:
        arguments (dict): A dictionary containing tool parameters.
                          Expects a 'url' key with the image URL.

    Yields:
        bytes: The raw binary content of the image.
        str: An error message if the fetch fails.
    """
    url = arguments.get('url')
    if not url:
        yield "ERROR: A 'url' argument is required to fetch an image."
        return

    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        yield response.content
    except requests.RequestException as e:
        yield f"ERROR: Could not fetch image from {url}. {e}"


def tool_render_html(arguments):
    """
    Receives an HTML string from the client and prints it to the server console.

    Args:
        arguments (dict): A dictionary containing tool parameters.
                          Expects an 'html_string' key.

    Yields:
        str: A success or error message.
    """
    html_content = arguments.get('html_string')
    if not html_content:
        yield "ERROR: 'html_string' argument is required."
        return

    print("\n--- Received HTML from Client ---")
    print(html_content)
    print("---------------------------------\n")

    yield "SUCCESS: HTML content received and printed to server console."

TOOL_REGISTRY = {
    "file_reader": tool_file_reader,
    "web_search": tool_web_search,
    "image_fetcher": tool_image_fetcher,
    "render_html": tool_render_html,
}

class ModuleContextServicer(mcs_pb2_grpc.ModuleContextServicer):
    """Provides implementations for the gRPC service methods."""

    def ListTools(self, request, context):
        """
        Handles the unary RPC to list all available tools.

        Args:
            request (mcs_pb2.ListToolsRequest): The incoming request object.
            context (grpc.ServicerContext): The gRPC context of the RPC.

        Returns:
            mcs_pb2.ListToolsResult: A message containing the list of tools.
        """
        print("Received ListTools request.")
        try:
            tools = [
                mcs_pb2.ToolDefinition(name="file_reader", description="Reads a local file."),
                mcs_pb2.ToolDefinition(name="web_search", description="Performs a web search using DuckDuckGo."),
                mcs_pb2.ToolDefinition(name="image_fetcher", description="Fetches an image from a URL."),
                mcs_pb2.ToolDefinition(name="render_html",
                                       description="Sends an HTML string to the server to be displayed.")
            ]
            return mcs_pb2.ListToolsResult(tools=tools)
        except Exception as e:
            print(f"❌ An unexpected error occurred in ListTools: {e}")
            print(traceback.format_exc())
            context.abort(grpc.StatusCode.INTERNAL, "An internal server error occurred.")

    def CallTool(self, request, context):
        """
        Handles the streaming RPC to dynamically call a tool by name.

        This method acts as a dispatcher. It looks up the requested tool in the
        TOOL_REGISTRY, calls the corresponding function, and streams the results
        back to the client, packaging each chunk into the appropriate message type.

        Args:
            request (mcs_pb2.ToolCallRequest): The request, containing tool name
                                               and arguments.
            context (grpc.ServicerContext): The gRPC context of the RPC.

        Yields:
            mcs_pb2.ToolCallChunk: Chunks of the tool's output, packaged in the
                                   appropriate 'oneof' field (text, html, or image).
        """
        print(f"Dispatching CallTool request for tool: {request.tool_name}")

        tool_function = TOOL_REGISTRY.get(request.tool_name)

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


def serve():
    """
    Configures and runs the secure gRPC server.

    This function handles loading environment variables, parsing command-line
    arguments, setting up the Keycloak authenticator and gRPC interceptor,
    loading TLS certificates, and starting the server.
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

        mcs_pb2_grpc.add_ModuleContextServicer_to_server(ModuleContextServicer(), server)

        server.add_secure_port(f'[::]:{args.port}', server_credentials)

        print(f"✅ Secure server started with authentication, listening on port {args.port}.")
        server.start()
        server.wait_for_termination()

    except FileNotFoundError:
        print("❌ Error: Certificate files (private.key, certificate.pem) not found in 'certs/' directory.")
    except Exception as e:
        print(f"❌ An error occurred during server startup: {e}")
        print(traceback.format_exc())


if __name__ == '__main__':
    serve()