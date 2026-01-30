# syntax=docker/dockerfile:1
FROM node:20-alpine

WORKDIR /app
ENV NODE_ENV=production

COPY package.json ./
RUN npm install --omit=dev

COPY src ./src
COPY schemas ./schemas

EXPOSE 8080
ENV PORT=8080

CMD ["node", "src/index.js"]
