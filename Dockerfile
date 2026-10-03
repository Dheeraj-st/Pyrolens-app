FROM node:26-alpine
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=http://localhost:8000/api/v1
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build
EXPOSE 3000
CMD ["npm", "run", "start"]
