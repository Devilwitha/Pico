#!/bin/sh
set -e
PICO_URL=$(jq -r '.pico_url // "http://192.168.1.100"' /data/options.json)
PICO_URL=${PICO_URL%/}
echo "Picodesk: leite Ingress an ${PICO_URL} weiter"
sed "s#__PICO_URL__#${PICO_URL}#g" /etc/nginx/picodesk.conf > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
