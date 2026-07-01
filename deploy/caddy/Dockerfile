FROM caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c
COPY Caddyfile /etc/caddy/Caddyfile
# Upstream caddy image ships a non-root caddy user (UID 1000) with /data and
# /config owned by caddy:caddy. CAP_NET_BIND_SERVICE (compose) allows :80/:443.
USER caddy
