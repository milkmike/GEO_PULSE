FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY .next/standalone ./
COPY .next/static ./.next/static
COPY public ./public

EXPOSE 3100
ENV PORT=3100
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
