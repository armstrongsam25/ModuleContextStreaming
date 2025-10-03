# In examples/llm_client.py
"""
An interactive client that connects an LLM to ModuleContextStreaming tools.
This example showcases the simplified workflow after moving logic into the client library.
"""
import os
import sys
from dotenv import load_dotenv
# Notice we only need to import the Client class now!
from ModuleContextStreaming.client import Client


def main():
	"""Main entry point."""
	load_dotenv()
	mcs_client = None

	try:
		# Get LLM API key
		llm_api_key = os.environ.get('LLM_API_KEY')
		if not llm_api_key:
			print("Error: LLM_API_KEY not found in environment", file=sys.stderr)
			sys.exit(1)

		# Connect to the MCS server (same as before)
		mcs_client = Client(
			server_address=f"{os.environ['MCS_SERVER_ADDRESS']}:{os.environ['MCS_PORT']}",
			keycloak_url=os.environ['KEYCLOAK_URL'],
			keycloak_realm=os.environ['KEYCLOAK_REALM'],
			keycloak_client_id=os.environ['KEYCLOAK_CLIENT_ID'],
			keycloak_client_secret=os.environ['KEYCLOAK_CLIENT_SECRET'],
			keycloak_audience=os.environ['KEYCLOAK_AUDIENCE']
		)

		# 1. Start the chat session directly from the client object
		chat_session = mcs_client.start_llm_chat_session(
			llm_api_key=llm_api_key,
			base_url=os.environ.get('LLM_API_URL'),
			model_name=os.environ.get('LLM_MODEL')
		)

		# 2. Run the interactive loop
		chat_session.run_interactive()

	except KeyError as e:
		print(f"❌ Error: Missing required environment variable: {e}", file=sys.stderr)
	except Exception as e:
		print(f"❌ An error occurred: {e}", file=sys.stderr)
		import traceback
		traceback.print_exc()
	finally:
		if mcs_client:
			mcs_client.close()


if __name__ == '__main__':
	main()