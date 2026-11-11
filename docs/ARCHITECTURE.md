# Enterprise System Architecture & Production Engineering

**Launch Intelligence Enterprise Platform** is a production-grade, highly available entertainment launch-analytics and predictive intelligence engine engineered for studio social, streaming, and theatrical marketing organizations.

---

## 1. High-Level Architecture Topology

```mermaid
flowchart TD
    subgraph ClientLayer ["1. Client & Ingress Layer"]
        USER["Browser UI / Executive Deck"]
        INGRESS["Kubernetes Ingress (Nginx + TLS)"]
        CLIENT["Automated API Clients / Microservices"]
    end

    subgraph ServiceMesh ["2. High-Availability Pod Mesh (Kubernetes)"]
        HPA["Horizontal Pod Autoscaler (HPA)<br/>(2 to 10 Replicas)"]
        FASTAPI["FastAPI Production ASGI Cluster<br/>(OpenAPI 3.1, Pydantic v2, Gunicorn/Uvicorn)"]
        CRON["K8s Collector CronJob<br/>(Scheduled Social Pipeline)"]
    end

    subgraph IntelligenceLayer ["3. AI/ML & Analytics Engine"]
        BASS["Bass Diffusion & Curve Fitting (Scipy/ML)<br/>(30-Day Velocity & Terminal Audience Projection)"]
        ANOMALY["Ensemble Anomaly Detector<br/>(Isolation Forest + Robust MAD + Bayesian Change-Point)"]
        NLP["Aspect Sentiment & Audience NLP<br/>(VFX, Cast, Story, Music, Hype & Bot Filtering)"]
        AGENT["Agentic Launch Analyst (Multi-Provider LLM)<br/>(Gemini / Claude / OpenAI / Offline Heuristic + Grounding)"]
        LEDGER["Fact Ledger & Claim Verification Engine<br/>(100% Deterministic Zero-Hallucination Gate)"]
    end

    subgraph StorageLayer ["4. Persistence & Caching"]
        REDIS[("Redis 7 (AOF Persistence)<br/>(Cluster Cache & Rate Limiting)")]
        PG[("PostgreSQL 16 (StatefulSet / RDS)<br/>(Relational Launch Snapshots & Events)")]
    end

    subgraph ObservabilityLayer ["5. Enterprise Observability"]
        PROM["Prometheus Server<br/>(ServiceMonitor Scraping /metrics)"]
        GRAFANA["Grafana Dashboards<br/>(Real-time Ingestion, Model Latency, Claim Accuracy)"]
    end

    USER --> INGRESS
    CLIENT --> INGRESS
    INGRESS --> FASTAPI
    HPA -.-> |Autoscale| FASTAPI

    FASTAPI --> REDIS
    FASTAPI --> PG
    CRON --> PG
    CRON --> REDIS

    FASTAPI --> IntelligenceLayer
    FASTAPI --> PROM
    PROM --> GRAFANA
```

---

## 2. Mathematical Formulations & Machine Learning Models

### 2.1 Bass Diffusion Trajectory Forecaster
Entertainment launch adoption behaves according to the Bass Diffusion Model, driven by an initial marketing spike (innovation coefficient $p$) followed by word-of-mouth momentum (imitation coefficient $q$):

$$F(t) = m \cdot \frac{1 - e^{-(p + q)t}}{1 + \frac{q}{p} e^{-(p + q)t}}$$

Where:
* $m$: Total ceiling market potential (views capacity).
* $p$: Coefficient of external advertising / PR shock ($0.01 \le p \le 0.40$).
* $q$: Coefficient of peer imitation and virality ($0.10 \le q \le 0.80$).

The parameter vector $\theta = [m, p, q]$ is estimated via nonlinear least squares (`scipy.optimize.curve_fit`):

$$\min_{\theta} \sum_{i=1}^{N} \left( V_i - F(t_i; \theta) \right)^2 \quad \text{subject to } m \ge \max(V), \; p > 0, \; q > 0$$

Confidence bounds at time horizon $t > t_{\text{observed}}$ expand dynamically as a function of forecast distance $\Delta t$:

$$\sigma(t) = V_{\text{pred}}(t) \cdot \left( \alpha_0 + \beta \cdot \Delta t \right)$$

---

### 2.2 Ensemble Anomaly & Episode Detection
Unlike legacy threshold alerts that generate noisy false positives, Launch Intelligence implements a multi-dimensional ensemble detector:

1. **Robust Median Absolute Deviation (MAD) Z-Score:**
   $$r_t = \ln\left( \frac{V_t + 1}{V_{t-1} + 1} \right)$$
   $$\text{MAD} = \text{median}\left( |r_t - \text{median}(r)| \right)$$
   $$\text{MAD}_{\text{floor}} = \max(\text{MAD}, 0.05)$$
   $$Z_t = \frac{0.6745 \cdot (r_t - \text{median}(r))}{\text{MAD}_{\text{floor}}}$$

2. **Multi-Feature Isolation Forest:**
   Features $\mathbf{x}_t = [\ln(V_t + 1), \; r_t, \; \Delta r_t]$ are partitioned into recursive random trees. The anomaly likelihood $P_{\text{IF}}(\mathbf{x}_t) \in [0, 1]$ measures path-length isolation depth.

3. **Episode Merging & Causal Linking:**
   Consecutive anomalous days are merged into a unified **Episode** (consolidating the initial surge and the subsequent rebound). Marketing events occurring within $\pm 48\text{h}$ are aligned with empirical lift:

$$\text{Lift Views} = V_{\text{peak}} - V_{\text{baseline}}$$
$$\text{Lift } \% = \frac{\text{Lift Views}}{V_{\text{baseline}}} \times 100$$

---

### 2.3 Aspect-Based Sentiment & Bot Filtering
Audience reactions on social video feeds are segmented across 5 core dimensions:
* **Visuals & CGI:** Cinematography, camera direction, VFX realism.
* **Cast & Performance:** Star appeal, acting delivery, character presence.
* **Story & Lore:** Plot intrigue, scriptwriting, IP/canon fidelity.
* **Music & Audio:** Soundtrack, themes, sound engineering.
* **Release Hype:** Ticket anticipation, day-one premiere excitement.

Automated regularized regex and token filters calculate positive/negative ratios per aspect and identify bot patterns (cryptocurrency spam, bio-link spam, unauthorized external links).

---

## 3. Kubernetes Deployment & Helm Architecture

The platform includes a production Helm v2 chart (`deploy/helm/launch-intelligence/`) and self-contained Kubernetes manifests (`deploy/k8s/complete-stack.yaml`):

### Key Specifications:
* **Autoscaling (HPA):** Dynamically scales from 2 to 10 pods when CPU utilization exceeds 70% or Memory exceeds 80%.
* **Security Hardening:** Pods execute with `runAsNonRoot: true`, non-root user UID 10001 (`appuser`), dropping all Linux capabilities (`drop: [ALL]`).
* **Zero-Downtime Rolling Updates:** `maxSurge: 1`, `maxUnavailable: 0` ensures seamless updates without dropping in-flight API requests.
* **Automated Data Ingestion:** A Kubernetes `CronJob` runs `0 */6 * * *` executing automated YouTube and social data collection pipelines.
* **Prometheus ServiceMonitor:** Automatically registered by the Prometheus Operator for 15-second metric scrape intervals.

---

## 4. Enterprise Observability & Prometheus Metrics

| Metric Name | Type | Description |
|---|---|---|
| `launch_intel_http_requests_total` | Counter | Total HTTP requests labeled by method, endpoint, and status code. |
| `launch_intel_http_request_duration_seconds` | Histogram | Request latency distributions per endpoint. |
| `launch_intel_ml_inference_seconds` | Histogram | Execution time for Bass Diffusion curve fitting and predictions. |
| `launch_intel_analytics_calc_seconds` | Histogram | Duration of cumulative series, SOV, and velocity calculation. |
| `launch_intel_nlp_sentiment_seconds` | Histogram | Time spent parsing comments and extracting aspect sentiment. |
| `launch_intel_ai_briefing_seconds` | Histogram | Latency for agentic causal reasoning and briefing generation. |

---

## 5. Production Operations Runbook

### Deploy with Helm
```bash
helm upgrade --install launch-intel deploy/helm/launch-intelligence \
  --namespace launch-intelligence \
  --create-namespace \
  --set env.DATABASE_URL="postgresql://user:pass@postgres:5432/launch_intel" \
  --set secrets.YOUTUBE_API_KEY="AIzaSy..." \
  --set secrets.GEMINI_API_KEY="AIzaSy..."
```

### Apply Raw Kubernetes Manifests
```bash
kubectl apply -f deploy/k8s/complete-stack.yaml
```

### Run Locally with Docker Compose
```bash
docker compose up -d --build
# Access UI & OpenAPI docs: http://localhost:8000/docs
# Access Prometheus: http://localhost:9090
# Access Grafana: http://localhost:3000 (admin / admin)
```

### CLI Predictive Inference
```bash
# Terminal ML forecast
launch-intel predict --title "Iron Meridian" --demo

# Terminal Agentic AI briefing memo
launch-intel briefing --title "Iron Meridian" --demo
```
