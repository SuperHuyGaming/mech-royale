FROM python:3.13-slim

WORKDIR /bot

ENV PYTHONDONTWRITEBYTECODE=1

COPY own/*.py ./

RUN chmod -R 755 /bot

USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
