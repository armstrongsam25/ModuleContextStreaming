# In ModuleContextStreaming/auth.py

import grpc
import requests
from jose import jwt
from jose.exceptions import JOSEError

from . import exceptions


class KeycloakAuthenticator:
	"""
	Handles the validation of JWT tokens issued by a Keycloak server.
	"""

	def __init__(self, server_url: str, realm: str, audience: str):
		self.audience = audience
		oidc_endpoint = f"{server_url}/realms/{realm}/.well-known/openid-configuration"

		try:
			# Fetch the OIDC config to find the JWKS URI for public keys
			config = requests.get(oidc_endpoint, timeout=5).json()
			self.jwks_uri = config["jwks_uri"]
			self.issuer = config["issuer"]

			# Fetch and cache the public keys (JWKS)
			self.jwks = requests.get(self.jwks_uri, timeout=5).json()
			print(f"✅ Authenticator initialized for issuer: {self.issuer}")
		except requests.exceptions.RequestException as e:
			print(f"❌ Could not connect to Keycloak at {server_url}. Please check the URL and your network connection.")
			raise e

	def validate_token(self, token: str) -> dict:
		"""
		Validates a JWT token from Keycloak.
		Returns the decoded claims if valid, otherwise raises AuthenticationFailed.
		"""
		try:
			# Decode the token, verifying signature, expiry, audience, and issuer
			claims = jwt.decode(
				token,
				self.jwks,
				algorithms=["RS256"],
				audience=self.audience,
				issuer=self.issuer,
			)
			return claims
		except JOSEError as e:
			# This catches signature errors, expired tokens, invalid claims, etc.
			raise exceptions.AuthenticationFailed(f"Token validation failed: {e}")


def _get_token_from_metadata(metadata):
	"""Extracts bearer token from gRPC metadata."""
	for key, value in metadata:
		if key == 'authorization' and value.startswith('Bearer '):
			return value[len('Bearer '):]
	return None


class AuthInterceptor(grpc.ServerInterceptor):
	def __init__(self, authenticator: KeycloakAuthenticator):
		self._authenticator = authenticator

	def intercept_service(self, continuation, handler_call_details):
		# --- MASTER DEBUG BLOCK ---
		# This will catch any error happening inside the interceptor
		try:
			method_name = handler_call_details.method.split('/')[-1]
			if method_name == 'Initialize':
				return continuation(handler_call_details)

			handler = continuation(handler_call_details)
			is_streaming = handler.response_streaming

			def _abort(status_code, details):
				def abort_handler(request, context):
					context.abort(status_code, details)
				if is_streaming:
					return grpc.unary_stream_rpc_method_handler(abort_handler)
				else:
					return grpc.unary_unary_rpc_method_handler(abort_handler)

			token = _get_token_from_metadata(handler_call_details.invocation_metadata)
			if not token:
				print("❌ Interceptor: Request rejected: Missing token.")
				return _abort(grpc.StatusCode.UNAUTHENTICATED, "Authorization token is missing.")

			try:
				claims = self._authenticator.validate_token(token)
				# TODO: Add claims to a custom context object that your service method can access.
				# This allows the RPC method itself to make authorization decisions.
				return handler
			except exceptions.AuthenticationFailed as e:
				print(f"❌ Interceptor: Request rejected: Invalid token. Reason: {e}")
				return _abort(grpc.StatusCode.UNAUTHENTICATED, "Token is invalid.")

		except Exception as e:
			# Abort the call so the client doesn't hang
			context = grpc.ServicerContext()
			context.abort(grpc.StatusCode.INTERNAL, "Fatal error occurred in authentication layer.")