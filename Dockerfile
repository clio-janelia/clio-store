FROM python:3.9-slim

ENV APP_HOME /app
WORKDIR $APP_HOME

# Copy requirements first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . ./

# Use hypercorn instead of uvicorn for better HTTP/2 support
CMD exec hypercorn main:app --bind 0.0.0.0:${PORT:-8080}