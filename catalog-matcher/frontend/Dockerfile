# Development image: installs deps and runs `next dev` with hot reload.
# Source is bind-mounted over this in docker-compose.yml; node_modules
# and .next stay inside the container via anonymous volumes (see
# docker-compose.yml) so a host-installed node_modules never clobbers
# the Linux-built one in the container.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 3000

CMD ["npx", "next", "dev", "-H", "0.0.0.0", "-p", "3000"]
