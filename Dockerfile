FROM python:3.11-slim

WORKDIR /app

# Force stdout/stderr to be unbuffered so print() output (bot connection
# status, retry messages) shows up in logs immediately instead of sitting
# in a buffer that may never flush in a containerized environment.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The application already has its own fresh-client exponential backoff.
# Disable discord.py's internal reconnect loop so a failed gateway handshake
# cannot try to resume a websocket that was never established (the source of
# the NoneType.sequence failure seen in production). The outer loop creates a
# fresh Client and backs off between attempts instead.
#
# For HTTP 429s, honor Discord's retry_after value instead of guessing with
# the generic exponential delay.
RUN sed -i 's/bot\.start(DISCORD_TOKEN)/bot.start(DISCORD_TOKEN, reconnect=False)/' app.py && sed -i '/if status == 429:/a\                retry_delay = max(float(getattr(e, "retry_after", retry_delay)), 1.0)' app.py

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
