# Stage 1 — builder
FROM node:20-slim AS builder

WORKDIR /app

COPY package.json .
RUN npm install --production


# Stage 2 — runtime
FROM node:20-slim

WORKDIR /app

COPY --from=builder /app/node_modules ./node_modules
COPY worker.js .
COPY package.json .

CMD ["node", "worker.js"]
