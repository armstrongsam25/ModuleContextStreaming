# In ModuleContextStreaming/exceptions.py

class MCPError(Exception):
    """Base exception for the library."""
    pass

class AuthenticationFailed(MCPError):
    """Raised when a token is invalid, expired, or missing."""
    pass