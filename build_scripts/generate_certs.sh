#!/bin/bash
# A minimalist script for generating self-signed certificates.

CERT_DIR="certs"
KEY_FILE="${CERT_DIR}/private.key"
CERT_FILE="${CERT_DIR}/certificate.pem"

# Create the certificate directory if it doesn't exist.
mkdir -p "$CERT_DIR"

# The core openssl command.
openssl req -x509 -newkey rsa:4096 -nodes -days 365 -keyout "$KEY_FILE" -out "$CERT_FILE" -subj "/CN=localhost"

# Simple confirmation message.
echo "Script finished. Check the '${CERT_DIR}' folder."