FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source file
COPY scraper.py .

# Force standard outputs to flush instantly into the Docker log stream
CMD ["python", "-u", "scraper.py"]