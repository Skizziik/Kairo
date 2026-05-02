# Dev-only Dockerfile — runs Vite dev server with HMR.
# For production we'll build static + serve via Caddy/nginx, but MVP needs hot-reload.
FROM node:20-alpine

WORKDIR /app

# Install deps first for better layer caching.
COPY package.json ./
RUN npm install --no-audit --no-fund

# Copy the rest. Mount as volume in compose for live reload.
COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
