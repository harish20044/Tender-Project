FROM node:20-slim

WORKDIR /app

# Dependency layer stays cached while only application code changes.
COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
