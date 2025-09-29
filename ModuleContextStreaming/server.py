# In ModuleContextStreaming/server.py
import traceback

import grpc
import time
import os
import argparse
from concurrent import futures
from dotenv import load_dotenv

from . import mcs_pb2
from . import mcs_pb2_grpc
from .auth import KeycloakAuthenticator, AuthInterceptor
import requests


# Create a class to define the server functions, derived from
# the generated base class.
class ModuleContextServicer(mcs_pb2_grpc.ModuleContextServicer):

	# Implement the Initialize RPC method
	def Initialize(self, request, context):
		print(f"Received Initialize request from: {request.client_info.name}")
		return mcs_pb2.InitializeResult(
			server_version="0.0.1-alpha",
			welcome_message=f"Hello {request.client_info.name}!"
		)

	# Implement the ListTools RPC method
	def ListTools(self, request, context):
		print("Received ListTools request.")
		try:
			# Create some placeholder tool definitions
			tools = [
				mcs_pb2.ToolDefinition(name="file_reader", description="Reads a local file."),
				mcs_pb2.ToolDefinition(name="code_interpreter", description="Executes a Python code snippet.")
			]
			return mcs_pb2.ListToolsResult(tools=tools)
		except Exception as e:
			# This block will now catch the specific Python error
			print(f"❌ An unexpected error occurred in ListTools: {e}")
			print(traceback.format_exc())  # Prints the full traceback
			# Abort the RPC with an internal error status
			context.abort(grpc.StatusCode.INTERNAL, "An internal server error occurred.")

	# Implement the StreamToolCall RPC method
	def StreamToolCall(self, request, context):
		print(f"Received StreamToolCall request for tool: {request.tool_name}")

		# Simulate a streaming response by yielding chunks of data
		for i in range(5):
			chunk = mcs_pb2.ToolCallChunk(
				content_chunk=f"This is chunk number {i + 1} for tool '{request.tool_name}'.\n",
				sequence_id=i
			)
			yield chunk
			time.sleep(0.5)  # Simulate work being done


# Function to create and run the server
def serve():
	# Load environment variables from a .env file if it exists
	load_dotenv()

	# Set up argument parser
	parser = argparse.ArgumentParser(description="Run the MCS gRPC server.")
	parser.add_argument(
		'--port',
		default=os.getenv('MCS_PORT', '50051'),
		help="Port to listen on (default: 50051, or from MCS_PORT env var)"
	)
	parser.add_argument(
		'--keycloak-url',
		default=os.getenv('KEYCLOAK_URL'),
		help="Keycloak server URL (overrides KEYCLOAK_URL env var)"
	)
	parser.add_argument(
		'--keycloak-realm',
		default=os.getenv('KEYCLOAK_REALM'),
		help="Keycloak realm name (overrides KEYCLOAK_REALM env var)"
	)
	parser.add_argument(
		'--keycloak-audience',
		default=os.getenv('KEYCLOAK_AUDIENCE'),
		help="Keycloak audience/client_id (overrides KEYCLOAK_AUDIENCE env var)"
	)
	args = parser.parse_args()

	# Validate that all required Keycloak settings are present
	if not all([args.keycloak_url, args.keycloak_realm, args.keycloak_audience]):
		print(
			"❌ Error: Keycloak URL, Realm, and Audience must be provided via .env file, environment variables, or command-line arguments.")
		return

	try:
		# Initialize our authenticator and interceptor from args
		authenticator = KeycloakAuthenticator(args.keycloak_url, args.keycloak_realm, args.keycloak_audience)
		auth_interceptor = AuthInterceptor(authenticator)

		server = grpc.server(
			futures.ThreadPoolExecutor(max_workers=10),
			interceptors=[auth_interceptor]
		)
	except requests.exceptions.RequestException:
		return

	mcs_pb2_grpc.add_ModuleContextServicer_to_server(ModuleContextServicer(), server)

	server.add_insecure_port(f"[::]:{args.port}")
	server.start()
	print(f"✅ Server started, listening on port {args.port}.")

	try:
		while True:
			time.sleep(86400)
	except KeyboardInterrupt:
		server.stop(0)
		print("🛑 Server stopped.")


if __name__ == '__main__':
	serve()