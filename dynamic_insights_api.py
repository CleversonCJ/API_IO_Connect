import threading
import time
from asyncio import Lock
from collections import defaultdict

import redis
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
import json

# Configuração da API da Meta
META_ACCESS_TOKEN = None
META_CLIENT_ID = "client_id"
META_CLIENT_SECRET = "client_secret"
META_REFRESH_TOKEN = "refresh_token"
META_BASE_URL = "https://graph.facebook.com/v21.0"

cache = redis.StrictRedis(host="localhost", port=6379, db=0)
rate_lock = Lock()
last_request_time = time.time()
REQUEST_COUNTER = {}
RATE_LIMIT = 5

app = FastAPI()

class InsightsRequest(BaseModel):
    id: str  # ID da campanha, grupo ou conta
    level: str
    start_date: str
    end_date: str

# Atualiza o token de acesso
def refresh_access_token():
    global META_ACCESS_TOKEN
    url = "https://graph.facebook.com/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": META_CLIENT_ID,
        "client_secret": META_CLIENT_SECRET,
        "fb_exchange_token": META_REFRESH_TOKEN,
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        META_ACCESS_TOKEN = data.get("access_token")
        print("[INFO] Novo token de acesso obtido.")
    except requests.exceptions.RequestException as e:
        print(f"[ERRO] Falha ao atualizar token de acesso: {e}")
        raise

# Valida o token de acesso antes de cada requisição
def validate_token():
    global META_ACCESS_TOKEN
    if META_ACCESS_TOKEN is None:
        refresh_access_token()
        return

    url = "https://graph.facebook.com/debug_token"
    params = {
        "input_token": META_ACCESS_TOKEN,
        "access_token": f"{META_CLIENT_ID}|{META_CLIENT_SECRET}",
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if not data.get("data", {}).get("is_valid"):
            print("[INFO] Token expirado ou inválido. Renovando...")
            refresh_access_token()
    except requests.exceptions.RequestException as e:
        print(f"[ERRO] Erro ao validar token: {e}")
        refresh_access_token()

# Agendar renovação automática do token
def schedule_token_refresh():
    def refresh_loop():
        while True:
            try:
                validate_token()
                time.sleep(3600)
            except Exception as e:
                print(f"[ERRO] Falha ao renovar token automaticamente: {e}")
                time.sleep(300)

    thread = threading.Thread(target=refresh_loop, daemon=True)
    thread.start()

# Gera o cabeçalho para cada requisição
def get_headers():
    validate_token()
    return {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}

# Faz uma requisição à API da Meta
def api_request(url, params):
    validate_token()
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao fazer requisição para {url}: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao fazer requisição: {str(e)}")

@app.post("/dynamic_insights")
async def fetch_dynamic_insights(insights_request: InsightsRequest):
    try:
        print("[DEBUG] Solicitação recebida:", insights_request.dict())

        level = insights_request.level.lower()
        if level not in ["account", "campaign", "adset"]:
            raise HTTPException(
                status_code=400,
                detail="Nível inválido. Deve ser 'account', 'campaign' ou 'adset'.",
            )

        id = insights_request.id
        start_date = insights_request.start_date
        end_date = insights_request.end_date

        print(f"[DEBUG] Buscando insights para {level} com ID {id}")
        print(f"[DEBUG] Período: {start_date} - {end_date}")

        # Busca Insights
        insights_url = f"{META_BASE_URL}/{id}/insights"
        params = {
            "fields": "reach,cpm,impressions,clicks,cpc,spend,cost_per_inline_link_click,inline_link_clicks",
            "time_range": json.dumps({"since": start_date, "until": end_date}),
            "level": level,
        }
        insights = api_request(insights_url, params).get("data", [])
        insights = api_request(insights_url, params).get("data", [])

        print(f"[DEBUG] Insights retornados: {insights}")

        return {
            "status": "success",
            "data": {
                "insights": insights,
            },
        }

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"[ERRO] Erro inesperado: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao processar solicitação: {str(e)}")
