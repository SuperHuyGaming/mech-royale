FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0
WORKDIR /bot
COPY mechfight.py bot.py ./
COPY mech/ ./mech/
RUN python3 -m compileall -q /bot && chmod -R a+rX /bot
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
