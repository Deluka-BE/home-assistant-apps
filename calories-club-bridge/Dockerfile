ARG BUILD_VERSION
ARG BUILD_ARCH

FROM ghcr.io/deluka-be/calories-club-node-red-bridge:v0.4.2

ARG BUILD_VERSION
ARG BUILD_ARCH

USER root
COPY run.js /app/addon-run.js
RUN chown node:node /app/addon-run.js

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

CMD ["node", "/app/addon-run.js"]
