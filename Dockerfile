FROM python:3.11-slim

WORKDIR /app

# Force stdout/stderr to be unbuffered so print() output (bot connection
# status, retry messages) shows up in logs immediately instead of sitting
# in a buffer that may never flush in a containerized environment.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
