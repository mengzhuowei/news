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

echo "[certbot] Resolving nginx container..."
until getent hosts nginx >/dev/null 2>&1; do
    echo "[certbot] nginx host not resolvable yet, waiting..."
    sleep 5
done
echo "[certbot] nginx resolves to: $(getent hosts nginx)"

echo "[certbot] Giving nginx 10 seconds to fully start..."
sleep 10

request_certificate() {
    if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
        echo "[certbot] Found existing certificate, attempting renew..."
        certbot renew --webroot -w /var/www/certbot --non-interactive
    else
        echo "[certbot] Requesting new certificate for $DOMAIN and $ROOT_DOMAIN..."
        certbot certonly --webroot -w /var/www/certbot \
            -d "$DOMAIN" \
            -d "$ROOT_DOMAIN" \
            --email "$CERT_EMAIL" \
            --agree-tos \
            --non-interactive
    fi
}

while :; do
    set +e
    request_certificate
    rc=$?
    set -e

    if [ $rc -ne 0 ]; then
        echo "[certbot] Request/renew failed (exit=$rc), retrying in 5 minutes..."
        sleep 300
        continue
    fi

    if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
        echo "[certbot] Certificate is ready at /etc/letsencrypt/live/${DOMAIN}/"
    else
        echo "[certbot] WARNING: Certificate not found after attempt. Will retry in 5 minutes..."
        sleep 300
        continue
    fi

    echo "[certbot] Next check in 12 hours..."
    sleep 43200 &
    wait $!
done
