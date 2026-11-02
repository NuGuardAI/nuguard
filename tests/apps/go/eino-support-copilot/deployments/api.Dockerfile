FROM golang:1.25.7-bookworm AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api ./cmd/api \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/mcp-server ./cmd/mcp-server \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/eval ./cmd/eval

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /out/api /out/mcp-server /out/eval /app/
COPY migrations /app/migrations
COPY seed /app/seed
USER app
EXPOSE 8080 8090
CMD ["/app/api"]
