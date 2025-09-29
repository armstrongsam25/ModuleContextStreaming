# In examples/simple_server.py
"""
An example runnable gRPC server that defines a specific set of tools.
"""
import requests
from ModuleContextStreaming.server import serve

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


if __name__ == '__main__':
    TOOL_REGISTRY = {
        "file_reader": tool_file_reader,
        "web_search": tool_web_search,
        "image_fetcher": tool_image_fetcher,
        "render_html": tool_render_html,
    }

    print("Starting example server...")
    serve(tool_registry=TOOL_REGISTRY)