FROM python:3.11-slim

# Install cron utility
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the scraper script
COPY scraper.py .

# Create the cron job inside the container to run every hour
RUN echo "0 * * * * cd /app && /usr/local/bin/python scraper.py >> /var/log/cron.log 2>&1" > /etc/cron.d/scraper-cron
RUN chmod 0644 /etc/cron.d/scraper-cron
RUN crontab /etc/cron.d/scraper-cron

# Run cron in the foreground to keep the container alive
CMD ["cron", "-f"]