FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Clone repository or copy files
# If GITHUB_REPO is provided, clone it; otherwise use COPY
ARG GITHUB_REPO
ARG GITHUB_BRANCH=main

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Initialize git if not already a repo (for Render deployments)
RUN if [ ! -d .git ] && [ -n "$GITHUB_REPO" ]; then \
        git init && \
        git remote add origin $GITHUB_REPO && \
        git fetch --depth=1 origin $GITHUB_BRANCH && \
        git reset --hard origin/$GITHUB_BRANCH; \
    fi

# Create non-root user for security
RUN useradd -m -u 1000 botuser && \
    chown -R botuser:botuser /app

# Switch to non-root user
USER botuser

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Run bot
CMD ["python", "bot.py"]
