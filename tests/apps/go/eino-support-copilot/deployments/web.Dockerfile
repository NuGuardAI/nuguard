FROM node:24-bookworm-slim AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM node:24-bookworm-slim AS build
ARG API_ORIGIN=http://api:8080
ENV API_ORIGIN=$API_ORIGIN
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web .
RUN npm run build

FROM node:24-bookworm-slim
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 API_ORIGIN=http://api:8080
WORKDIR /app
RUN useradd --create-home --uid 10001 app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
USER app
EXPOSE 3000
CMD ["node", "server.js"]
