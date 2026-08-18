FROM node:20-alpine AS builder
# No build step needed — pure static HTML/JS (Tailwind via CDN)
WORKDIR /app
COPY index.html app.js ./

FROM nginx:1.25-alpine
LABEL org.opencontainers.image.title="FinTech GOAT - Frontend UI"
LABEL org.opencontainers.image.description="Tailwind-based FinTech dashboard for NuGuard red-team testing"

COPY --from=builder /app/index.html /usr/share/nginx/html/
COPY --from=builder /app/app.js     /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/nginx.conf.template

EXPOSE 80

# envsubst resolves ${ORCHESTRATOR_UPSTREAM} at container start from the env var
CMD ["/bin/sh", "-c", "envsubst '${ORCHESTRATOR_UPSTREAM}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf && nginx -g 'daemon off;'"]
