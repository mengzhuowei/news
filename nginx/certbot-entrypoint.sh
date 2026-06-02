#!/bin/sh
set -e

RAW_DOMAIN=$(echo "$SITE_BASEURL" | sed -e 's|^https\?://||' -e 's|/.*$||')

if [ -z "$RAW_DOMAIN" ]; then
    echo "[certbot] ERROR: SITE_BASEURL is not set or does not contain a valid domain"
    exit 1
fi

if echo "$RAW_DOMAIN" | grep -q '^www\.'; then
    DOMAIN="$RAW_DOMAIN"
    ROOT_DOMAIN=$(echo "$RAW_DOMAIN" | sed 's/^www\.//')
else
    DOMAIN="www.${RAW_DOMAIN}"
    ROOT_DOMAIN="$RAW_DOMAIN"
fi

CERT_EMAIL="${CERTBOT_EMAIL:-admin@${DOMAIN}}"

echo "[certbot] Domain (www): $DOMAIN"
echo "[certbot] Root domain: $ROOT_DOMAIN"
echo "[certbot] Email: $CERT_EMAIL"

trap exit TERM

while :; do
    echo "[certbot] Waiting for nginx to be ready..."
    until wget -q -O /dev/null "http://nginx/.well-known/acme-challenge/" 2>/dev/null || \
          wget -q -O /dev/null "http://nginx/" 2>/dev/null; do
        sleep 5
    done
    sleep 5

    if [ -d "/etc/letsencrypt/live/${DOMAIN}" ] && \
       ! openssl x509 -checkend 2592000 -noout -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" 2>/dev/null; then
        echo "[certbot] Existing certificate is valid for more than 30 days, skipping initial request."
    elif [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
        echo "[certbot] Certificate exists but expires soon, attempting renewal..."
        certbot renew --webroot -w /var/www/certbot --non-interactive --quiet
    else
        echo "[certbot] Requesting new certificate for $DOMAIN and $ROOT_DOMAIN..."
        certbot certonly --webroot -w /var/www/certbot \
            -d "$DOMAIN" \
            -d "$ROOT_DOMAIN" \
            --email "$CERT_EMAIL" \
            --agree-tos \
            --non-interactive \
            --quiet
    fi

    if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
        echo "[certbot] Certificate is ready."
    else
        echo "[certbot] WARNING: Certificate not found after attempt. Will retry."
    fi

    echo "[certbot] Next check in 12 hours..."
    sleep 43200 &
    wait $!
done
