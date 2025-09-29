# Minimalist script to avoid encoding and character issues.

$CertDir = "certs"
$KeyFile = Join-Path $CertDir "private.key"
$CertFile = Join-Path $CertDir "certificate.pem"

# Create directory if it doesn't exist
if (-not (Test-Path -Path $CertDir -PathType Container)) {
    New-Item -ItemType Directory -Path $CertDir | Out-Null
}

# The core openssl command
openssl req -x509 -newkey rsa:4096 -nodes -days 365 -keyout $KeyFile -out $CertFile -subj "/CN=localhost"

# Simple, unindented output
Write-Host "Script finished. Check the '$CertDir' folder."