# Deploy to GCP Cloud Run

This demo is a single public Cloud Run service: FastAPI serves both the API and
the React static frontend.

## 1. Configure project variables

```bash
export PROJECT_ID="your-gcp-project"
export REGION="asia-southeast1"
export SERVICE="trust-analytics-portal"
export REPO="trust-analytics"
gcloud config set project "$PROJECT_ID"
```

## 2. Store the OpenAI key

```bash
printf "%s" "$OPENAI_API_KEY" | gcloud secrets create openai-api-key --data-file=-
```

If the secret already exists:

```bash
printf "%s" "$OPENAI_API_KEY" | gcloud secrets versions add openai-api-key --data-file=-
```

## 3. Build and push

```bash
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" || true

gcloud builds submit \
  --tag "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:latest"
```

## 4. Deploy

```bash
gcloud run deploy "$SERVICE" \
  --image "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE:latest" \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars OPENAI_MODEL=gpt-4.1-mini,TRUST_ANALYTICS_DATA_DIR=demo_data/fintech_analytics/data,TRUST_ANALYTICS_DB_PATH=var/trust_analytics.sqlite \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest
```

## 5. Verify

```bash
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
curl "$SERVICE_URL/api/health"
open "$SERVICE_URL"
```

The UI will use a live OpenAI run when available and fall back to verified
cached results if the live path fails.
