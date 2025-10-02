# In tests/conftest.py

import grpc
import pytest
import threading
from datetime import datetime, timezone, timedelta
from concurrent import futures
from unittest.mock import MagicMock

# --- Imports from your project ---
from ModuleContextStreaming import mcs_pb2_grpc
from ModuleContextStreaming.server import ModuleContextServicer


# --- Fixtures for Mocking and Setup ---

@pytest.fixture(scope="session")
def mock_tool_registry():
	"""A fixture providing a dictionary of mock tool functions for testing."""

	def tool_text_responder(arguments):
		"""A simple tool that yields text."""
		yield "Text response 1"
		yield "Text response 2"

	def tool_bytes_responder(arguments):
		"""A simple tool that yields bytes."""
		yield b"\x89PNG\r\n\x1a\n\x00\x00"

	return {
		"text_tool": tool_text_responder,
		"bytes_tool": tool_bytes_responder,
	}


@pytest.fixture(scope="session")
def testing_certs(tmp_path_factory):
	"""
	Creates temporary self-signed certificates for testing secure connections.
	This is a session-scoped fixture, so it runs only once per test session.
	"""
	from cryptography import x509
	from cryptography.x509.oid import NameOID
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import rsa

	certs_dir = tmp_path_factory.mktemp("certs")
	key_path = certs_dir / "private.key"
	cert_path = certs_dir / "certificate.pem"

	key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	with open(key_path, "wb") as f:
		f.write(key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.TraditionalOpenSSL,
			encryption_algorithm=serialization.NoEncryption(),
		))

	subject = issuer = x509.Name([
		x509.NameAttribute(NameOID.COUNTRY_NAME, u"US"),
		x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"TestState"),
		x509.NameAttribute(NameOID.LOCALITY_NAME, u"TestCity"),
		x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"TestOrg"),
		x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
	])
	cert = (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(issuer)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		# --- MODIFIED: Replaced deprecated utcnow() ---
		.not_valid_before(datetime.now(timezone.utc))
		.not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
		.add_extension(x509.SubjectAlternativeName([x509.DNSName(u"localhost")]), critical=False)
		.sign(key, hashes.SHA256())
	)
	with open(cert_path, "wb") as f:
		f.write(cert.public_bytes(serialization.Encoding.PEM))

	return {"key_path": str(key_path), "cert_path": str(cert_path)}


# --- MODIFIED: Removed scope="module" to fix ScopeMismatch ---
@pytest.fixture
def grpc_server(mock_tool_registry, testing_certs, monkeypatch):
	"""
	Starts a gRPC server in a background thread for testing.
	This is now function-scoped to match the 'monkeypatch' fixture.
	"""
	mock_authenticator = MagicMock()
	mock_authenticator.validate_token.return_value = {"sub": "test-user"}
	monkeypatch.setattr("ModuleContextStreaming.server.KeycloakAuthenticator",
						lambda *args, **kwargs: mock_authenticator)

	server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
	servicer = ModuleContextServicer(mock_tool_registry)
	mcs_pb2_grpc.add_ModuleContextServicer_to_server(servicer, server)

	with open(testing_certs["key_path"], 'rb') as f:
		private_key = f.read()
	with open(testing_certs["cert_path"], 'rb') as f:
		certificate_chain = f.read()
	server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))

	port = server.add_secure_port('[::]:0', server_credentials)

	server_thread = threading.Thread(target=server.start)
	server_thread.daemon = True
	server_thread.start()

	yield f'localhost:{port}'

	server.stop(grace=0)
	server_thread.join()