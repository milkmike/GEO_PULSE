FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* inlines at build time; empty default → same-host:8100 at runtime
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Cap node heap: prod builds run on the live server next to ~20 containers —
# an unbounded webpack heap OOM-froze the box once (2026-06-12). 1.5G is
# plenty for this app and leaves headroom for everything else.
ENV NODE_OPTIONS=--max-old-space-size=1536
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3334 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3334
CMD ["node", "server.js"]
