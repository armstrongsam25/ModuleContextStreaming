# In examples/simple_server.py
"""
An example runnable gRPC server that defines a specific set of tools
and connects to an MCP backend.
"""
import os
import sys

import requests
from dotenv import load_dotenv
from ddgs import DDGS

# This import remains the same, pointing to the refactored Server class
from ModuleContextStreaming.server import Server


# --- Native Python Tool Definitions ---

def tool_file_reader(arguments):
    """Reads a file and streams its content."""
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
    """Performs a web search using DuckDuckGo."""
    query = arguments.get('query')
    if not query:
        yield "ERROR: A 'query' argument is required."
        return
    try:
        results = list(DDGS().text(query, max_results=5))
        if results:
            yield "\nTop Search Results:"
            for r in results:
                yield f"- {r.get('title')}: {r.get('href')}"
        else:
            yield "No results found."
    except Exception as e:
        yield f"ERROR: An exception occurred during web search: {e}"


def tool_image_fetcher(arguments):
    """Fetches an image from a URL."""
    url = arguments.get('url')
    if not url:
        yield "ERROR: A 'url' argument is required."
        return
    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        yield response.content
    except requests.RequestException as e:
        yield f"ERROR: Could not fetch image from {url}. {e}"

def tool_render_html(arguments):
    """Renders HTML using a remote server."""
    html = arguments.get('html_string')
    if not html:
        yield "ERROR: A 'html' argument is required."
        return
    try:
        yield "Rendering HTML..."
        yield html
        yield "Rendering complete."
    except requests.RequestException as e:
        yield f"ERROR: Could not render HTML. {e}"


if __name__ == '__main__':
    load_dotenv()
    print("🚀 Starting example server...")

    # A registry for your native Python tools.
    NATIVE_TOOL_REGISTRY = {
        "file_reader": tool_file_reader,
        "web_search": tool_web_search,
        "image_fetcher": tool_image_fetcher,
        "render_html": tool_render_html
    }

    # TODO: configure MCP server defs to work with MCS.
    MCP_BACKENDS_CONFIG = None

    try:
        server_port = int(os.environ['MCS_PORT'])
        kc_url = os.environ['KEYCLOAK_URL']
        kc_realm = os.environ['KEYCLOAK_REALM']
        kc_audience = os.environ['KEYCLOAK_AUDIENCE']
    except KeyError as e:
        print(f"❌ Error: Missing required environment variable: {e}", file=sys.stderr)
        sys.exit(1)

    # Instantiate the Server with native tools and MCP backends
    server_instance = Server(
        tool_registry=NATIVE_TOOL_REGISTRY,
        port=server_port,
        keycloak_url=kc_url,
        keycloak_realm=kc_realm,
        keycloak_audience=kc_audience,
        # key_path='certs/private.key',
        # cert_path='certs/certificate.pem',
        mcp_backends=MCP_BACKENDS_CONFIG  # Add the MCP configuration here
    )

    # Run the server
    server_instance.run()