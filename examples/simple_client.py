# In examples/simple_client.py
"""
An example runnable gRPC client that uses the ModuleContextStreaming library.
"""
import os
import sys
import uuid
from dotenv import load_dotenv
from ModuleContextStreaming.client import Client


def main():
	"""Sets up the client and runs a sequence of example tool calls."""
	load_dotenv()
	client = None  # Initialize client to None

	try:
		# Load all configuration from environment variables
		server_address = f"{os.environ['MCS_SERVER_ADDRESS']}:{os.environ['MCS_PORT']}"
		kc_url = os.environ['KEYCLOAK_URL']
		kc_realm = os.environ['KEYCLOAK_REALM']
		kc_client_id = os.environ['KEYCLOAK_CLIENT_ID']
		kc_client_secret = os.environ['KEYCLOAK_CLIENT_SECRET']
		kc_audience = os.environ['KEYCLOAK_AUDIENCE']

		# 1. Instantiate the Client, which handles connection and auth.
		client = Client(
			server_address=server_address,
			cert_path='certs/certificate.pem',
			keycloak_url=kc_url,
			keycloak_realm=kc_realm,
			keycloak_client_id=kc_client_id,
			keycloak_client_secret=kc_client_secret,
			keycloak_audience=kc_audience
		)

		# 2. Use the client's methods to interact with the server.
		tools = client.list_tools()
		for tool in tools:
			print(f"  - {tool.name}: {tool.description}")

		# --- Example Tool Calls ---

		# Example 1: Web Search
		search_query = "gRPC Python"
		for chunk in client.call_tool("web_search", {"query": search_query}):
			if chunk.WhichOneof('content_block') == 'text':
				print(f"  [Search Result] {chunk.text.text.strip()}")

		# Example 2: Image Fetcher
		image_url = "https://www.python.org/static/community_logos/python-logo-master-v3-TM.png"
		output_filename = f"{uuid.uuid4()}.png"
		for chunk in client.call_tool("image_fetcher", {"url": image_url}):
			if chunk.WhichOneof('content_block') == 'image':
				with open(output_filename, 'wb') as f:
					f.write(chunk.image.data)
				print(f"  [Image] Saved {len(chunk.image.data)} bytes to {output_filename}")

		# Example 3: Render HTML
		html_content = "<h1>Hello, World!</h1>"
		for chunk in client.call_tool("render_html", {"html_string": html_content}):
			if chunk.WhichOneof('content_block') == 'text':
				print(f"  [Rendered HTML] {chunk.text.text.strip()}")

		# Example 4: File Reader
		file_path = "LICENSE.md"
		for chunk in client.call_tool("file_reader", {"path": file_path}):
			if chunk.WhichOneof('content_block') == 'text':
				print(f"  [File Content] {chunk.text.text.strip()}")


	except KeyError as e:
		print(f"❌ Error: Missing required environment variable in .env file: {e}", file=sys.stderr)
	except Exception as e:
		print(f"❌ An application error occurred: {e}", file=sys.stderr)
	finally:
		# 3. Ensure the connection is closed.
		if client:
			client.close()


if __name__ == '__main__':
	main()