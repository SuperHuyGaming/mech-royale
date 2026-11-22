FROM python:3.13-slim

WORKDIR /bot

ENV PYTHONDONTWRITEBYTECODE=1

COPY *.py /bot/

RUN chmod -R 755 /bot

ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
