FROM --platform=linux/arm64 public.ecr.aws/docker/library/node:20-slim

WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev

COPY tsconfig.json ./
COPY src ./src

RUN npm install --save-dev typescript @types/express @types/node
RUN npx tsc

EXPOSE 8080
CMD ["node", "dist/index.js"]
