FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py bot.py ./
RUN chmod -R 755 /bot
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
