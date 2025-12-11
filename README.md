# polypy

## Docker Development

### Quick Start with Docker Compose

Run the application with Prometheus monitoring:

```bash
# Build and start the stack
just stack-up

# View logs
just docker-logs

# Stop the stack
just stack-down

# Clean up everything (including volumes)
just docker-clean
```

### Services

- **PolyPy**: http://localhost:8080
  - Stats endpoint: http://localhost:8080/stats
  - Health endpoint: http://localhost:8080/health
  - Metrics endpoint: http://localhost:8080/metrics (added in Phase 1)
- **Prometheus**: http://localhost:9090
  - Scrapes PolyPy metrics every 15 seconds
  - 1 hour data retention

### Docker Commands

```bash
# Build image only
just docker-build

# Start services
just docker-up

# Stop services
just docker-down

# View logs
just docker-logs

# Restart services
just docker-restart
```