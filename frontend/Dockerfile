FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

# Dev-Server für das lokale Werkstatt-Setup (Kap. 1.2 Phase 1). Für eine
# spätere Produktions-Bereitstellung: mehrstufiger Build + Nginx/static-Serve.
CMD ["npm", "run", "dev"]
