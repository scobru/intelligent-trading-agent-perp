FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=3000 \
    SYNFUTURES_PORT=3100 \
    SYNFUTURES_SERVICE_URL=http://localhost:3100

# Install system dependencies, SQLite3, git, build tools and Node.js 20
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    build-essential \
    git \
    sqlite3 \
    dos2unix \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && npm install -g typescript ts-node \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Install and build Node.js SynFutures microservice
WORKDIR /app/synfutures-service
COPY synfutures-service/package*.json synfutures-service/tsconfig.json ./
RUN npm install --include=dev
COPY synfutures-service/src ./src
RUN npm run build

# 2. Install Python dependencies
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 3. Copy project files
COPY . .

# Ensure start.sh has Unix LF line endings and execution permissions
RUN dos2unix ./start.sh && chmod +x ./start.sh

# Directory for persistent SQLite database
RUN mkdir -p /app/data

EXPOSE 3000 3100

CMD ["/bin/bash", "./start.sh"]
