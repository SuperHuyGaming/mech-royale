FROM python:3.13-slim
WORKDIR /bot
COPY own/mechfight.py own/bot.py ./
RUN chmod -R 755 /bot
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
