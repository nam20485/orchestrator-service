FROM caddy:2.10.0-alpine@sha256:ae4458638da8e1a91aafffb231c5f8778e964bca650c8a8cb23a7e8ac557aa3c
COPY Caddyfile /etc/caddy/Caddyfile
COPY caddy-entrypoint.sh /caddy-entrypoint.sh

# The pinned upstream image runs as ROOT and ships NO non-root user (verified:
# /etc/passwd has only standard Alpine users, Config.User is empty). Create a
# caddy user and own the writable dirs so Caddy can run non-root. Compose grants
# CAP_NET_BIND_SERVICE (bounding set) for :80/:443; the file capability below
# lets the non-root binary actually use it after the su-exec drop.
RUN apk add --no-cache su-exec libcap \
 && addgroup -S caddy \
 && adduser -S -D -H -G caddy caddy \
 && chmod +x /caddy-entrypoint.sh \
 && chown -R caddy:caddy /data /config /srv /etc/caddy \
 && setcap cap_net_bind_service=+ep /usr/bin/caddy

# Start as root so the entrypoint can chown root-owned named volumes on first
# mount, then drop to the caddy user via su-exec before running Caddy.
ENTRYPOINT ["/caddy-entrypoint.sh"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
