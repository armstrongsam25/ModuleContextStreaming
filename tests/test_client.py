# In tests/test_client.py

import pytest
import requests
from unittest.mock import MagicMock # <-- MODIFIED: Added import
from ModuleContextStreaming.client import Client

# --- Test Cases ---

def test_client_initialization_success(grpc_server, testing_certs, monkeypatch):
    """
    Verify the client can be initialized successfully with valid parameters.
    """
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MagicMock(
        raise_for_status=lambda: None,
        json=lambda: {"access_token": "dummy-token"}
    ))

    client = None
    try:
        client = Client(
            server_address=grpc_server,
            cert_path=testing_certs['cert_path'],
            keycloak_url="http://fake-keycloak",
            keycloak_realm="test",
            keycloak_client_id="test",
            keycloak_client_secret="test",
            keycloak_audience="test"
        )
        assert client.stub is not None
        assert client.channel is not None
    finally:
        if client:
            client.close()

def test_client_initialization_auth_fails(monkeypatch):
    """
    Verify the client raises a ConnectionError if Keycloak auth fails.
    """
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MagicMock(
        raise_for_status=lambda: exec('raise requests.exceptions.HTTPError("401 Client Error")')
    ))

    with pytest.raises(ConnectionError, match="Failed to authenticate with Keycloak"):
        Client(
            server_address="localhost:1234",
            cert_path="dummy.pem",
            keycloak_url="http://fake-keycloak",
            keycloak_realm="test",
            keycloak_client_id="test",
            keycloak_client_secret="test",
            keycloak_audience="test"
        )

def test_client_initialization_cert_not_found(monkeypatch):
    """
    Verify the client raises a FileNotFoundError if the cert file is missing.
    """
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MagicMock(
        raise_for_status=lambda: None,
        json=lambda: {"access_token": "dummy-token"}
    ))

    with pytest.raises(FileNotFoundError, match="Certificate file not found at 'non_existent.pem'"):
        Client(
            server_address="localhost:1234",
            cert_path="non_existent.pem",
            keycloak_url="http://fake-keycloak",
            keycloak_realm="test",
            keycloak_client_id="test",
            keycloak_client_secret="test",
            keycloak_audience="test"
        )

@pytest.fixture
def fully_initialized_client(grpc_server, testing_certs, monkeypatch):
    """Fixture to provide a fully initialized and connected client instance."""
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: MagicMock(
        raise_for_status=lambda: None,
        json=lambda: {"access_token": "dummy-token"}
    ))
    client = Client(
        server_address=grpc_server,
        cert_path=testing_certs['cert_path'],
        keycloak_url="http://fake-keycloak",
        keycloak_realm="test",
        keycloak_client_id="test",
        keycloak_client_secret="test",
        keycloak_audience="test"
    )
    yield client
    client.close()


def test_client_list_tools(fully_initialized_client, mock_tool_registry):
    """
    Verify the client's list_tools method correctly calls and parses the response.
    """
    tools = fully_initialized_client.list_tools()
    assert len(tools) == len(mock_tool_registry)
    tool_names = {tool.name for tool in tools}
    assert tool_names == set(mock_tool_registry.keys())

def test_client_call_tool(fully_initialized_client):
    """
    Verify the client's call_tool method correctly streams and yields results.
    """
    chunks = list(fully_initialized_client.call_tool("text_tool", {}))
    assert len(chunks) == 2
    assert chunks[0].text.text == "Text response 1"