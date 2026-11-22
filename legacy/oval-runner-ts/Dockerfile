FROM node:24.19.0-bookworm-slim
WORKDIR /bot
COPY package.json bot.ts mechfight.ts nav.mjs geo.mjs ./
USER node
ENTRYPOINT ["node", "/bot/bot.ts"]
