# Swim Recruiting Analyzer

AI-powered swim recruiting analysis tool for swimmers and coaches.

## Project Overview

A web app that takes a swimmer's profile (times, academics, preferences) and uses OpenAI GPT-4o to generate a detailed recruiting analysis including tier placement, program recommendations, and improvement areas.

## Architecture

- **Backend**: Python Flask on port 5000
- **Frontend**: Static HTML/CSS/JS served by Flask from `static/`
- **AI**: OpenAI GPT-4o via the `openai` Python package

## Key Files

- `main.py` — Flask app, `/api/analyze` endpoint, serves static files
- `static/index.html` — Full single-page frontend UI
- `requirements.txt` — Python dependencies

## Environment Variables

- `OPENAI_API_KEY` — Required for AI analysis (set in Secrets)

## Running Locally

```bash
python main.py
```

App runs on `http://0.0.0.0:5000`

## Deployment

Configured for autoscale deployment with gunicorn:
```
gunicorn --bind=0.0.0.0:5000 --reuse-port main:app
```
