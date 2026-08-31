FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package*.json ./
RUN npm install
FROM node:22-alpine
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
