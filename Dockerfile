FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Keep the bot's fresh-client strategy, and patch the actual run_until_complete
# call used by app.py. Log the server-provided retry_after without nested f-string
# quoting so the generated Python remains valid.
RUN sed -i 's/bot\.start(DISCORD_TOKEN))/bot.start(DISCORD_TOKEN, reconnect=False))/' app.py && sed -i '/if status == 429:/a\                retry_delay = max(float(getattr(e, "retry_after", retry_delay)), 1.0)' app.py && sed -i '/if status == 429:/a\                print("[bot] 429 details: retry_after=%s headers=%s" % (getattr(e, "retry_after", None), getattr(getattr(e, "response", None), "headers", {})), flush=True)' app.py

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
