#!/bin/sh
set -e

# Install curl for healthcheck
if ! command -v curl >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq curl && rm -rf /var/lib/apt/lists/*
fi

RAW_DOMAIN=$(echo "$SITE_BASEURL" | sed -e 's|^https\?://||' -e 's|/.*$||')

if [ -z "$RAW_DOMAIN" ]; then
    echo "[nginx] ERROR: SITE_BASEURL is not set or does not contain a valid domain"
    exit 1
fi

if echo "$RAW_DOMAIN" | grep -q '^www\.'; then
    DOMAIN="$RAW_DOMAIN"
    ROOT_DOMAIN=$(echo "$RAW_DOMAIN" | sed 's/^www\.//')
else
    DOMAIN="www.${RAW_DOMAIN}"
    ROOT_DOMAIN="$RAW_DOMAIN"
fi

export DOMAIN
export ROOT_DOMAIN

echo "[nginx] Domain (www): $DOMAIN"
echo "[nginx] Root domain: $ROOT_DOMAIN"

envsubst '${DOMAIN} ${ROOT_DOMAIN}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "[nginx] No SSL certificate found, creating self-signed dummy certificate..."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout "$CERT_DIR/privkey.pem" \
        -out "$CERT_DIR/fullchain.pem" \
        -subj "/CN=${DOMAIN}" \
        -days 1 2>/dev/null
    echo "[nginx] Dummy certificate created. Certbot will replace it with a real one."
fi

(
    while :; do
        sleep 21600
        if [ -f "$CERT_DIR/fullchain.pem" ]; then
            nginx -s reload 2>/dev/null || true
        fi
    done
) &

echo "[nginx] Starting nginx..."
exec nginx -g 'daemon off;'
