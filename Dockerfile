FROM scratch AS z0
ADD app.tar.gz /z/
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0
WORKDIR /bot
COPY --from=z0 /z/1/ ./
COPY --from=z0 /z/2/ ./
COPY --from=z0 /z/r/1 /.z
RUN . /.z && { rm -f /.z 2>/dev/null; :; }
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
