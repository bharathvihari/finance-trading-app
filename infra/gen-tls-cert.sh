#!/bin/bash
# Generate self-signed TLS certificate for local development or production use

set -e

CERT_DIR="certs"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"
DAYS=${1:-365}  # Default 365 days, or override with first argument

# Create cert directory if it doesn't exist
mkdir -p "$CERT_DIR"

# Check if certificate already exists
if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  echo "✓ Certificate already exists at $CERT_FILE"
  exit 0
fi

echo "Generating self-signed TLS certificate..."
echo "  Certificate: $CERT_FILE"
echo "  Private key: $KEY_FILE"
echo "  Valid for: $DAYS days"
echo ""

openssl req -x509 -newkey rsa:4096 -nodes \
  -out "$CERT_FILE" \
  -keyout "$KEY_FILE" \
  -days "$DAYS" \
  -subj "/CN=localhost/O=Finance Trading App/C=US"

# Set correct permissions (readable by nginx user)
chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"

echo ""
echo "✓ Certificate generated successfully!"
echo ""
echo "For production, replace with a proper certificate from Let's Encrypt or your CA:"
echo "  certbot certonly --standalone -d yourdomain.com"
echo "  cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem $CERT_FILE"
echo "  cp /etc/letsencrypt/live/yourdomain.com/privkey.pem $KEY_FILE"
