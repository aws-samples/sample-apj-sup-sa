# --- build stage: compile the React/Vite SPA ---
FROM public.ecr.aws/docker/library/node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- serve stage: static files via nginx ---
FROM public.ecr.aws/docker/library/nginx:1.27-alpine
# Patch OS packages to the latest available in the Alpine branch to remediate
# base-image CVEs flagged by Amazon Inspector (openssl, expat, curl, libpng,
# libxml2, zlib, ...). Rebuild with a freshly pulled base so this stays current.
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
# Entrypoint hook: writes /auth-config.json from ECS task env vars at startup.
COPY 40-auth-config.sh /docker-entrypoint.d/40-auth-config.sh
RUN chmod +x /docker-entrypoint.d/40-auth-config.sh
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
