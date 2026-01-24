import os
import re
import asyncio
import json
import time
import mimetypes
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- Configuración y Variables de Entorno ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://consulta-pe-bot.up.railway.app").rstrip("/")
SESSION_STRING = os.getenv("SESSION_STRING", None)
PORT = int(os.getenv("PORT", 8080))

# ID del canal
CHANNEL_ID = -1001507924325 

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Cliente Telegram Global ---
client = None

async def get_client():
    """Obtiene o inicializa el cliente de Telegram de forma segura"""
    global client
    if client is None:
        if API_ID == 0 or not API_HASH or not SESSION_STRING:
            raise Exception("Credenciales de Telegram no configuradas.")
        session = StringSession(SESSION_STRING)
        client = TelegramClient(session, API_ID, API_HASH)
    
    if not client.is_connected():
        await client.connect()
        
    return client

# --- Lógica de Búsqueda ---
async def search_movies_in_channel(search_query: str):
    try:
        t_client = await get_client()
        search_query = search_query.lower().strip()
        results = []
        
        # Buscar en los últimos 200 mensajes
        async for message in t_client.iter_messages(CHANNEL_ID, limit=200):
            if not message.text:
                continue
            
            message_text = message.text.lower()
            if search_query in message_text or any(word in message_text for word in search_query.split()):
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
        
        return results
    except Exception as e:
        print(f"Error buscando películas: {str(e)}")
        return []

def extract_movie_info(message):
    try:
        text = message.text
        patterns = {
            "title": r"(?:Título|Película|Movie)[:\-]\s*(.+?)(?:\n|$)",
            "year": r"(?:Año|Year)[:\-]\s*(\d{4})",
            "quality": r"(?:Calidad|Quality)[:\-]\s*(.+?)(?:\n|$)",
            "size": r"(?:Tamaño|Size)[:\-]\s*(.+?)(?:\n|$)"
        }
        
        info = {"message_id": message.id, "text_preview": text[:200]}
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[key] = match.group(1).strip()
        
        # Extraer links
        links = re.findall(r"(https?://[^\s]+)", text)
        if links:
            info["download_links"] = [{"url": l, "type": "link"} for l in links]
            
        if not info.get("title"):
            info["title"] = text.split('\n')[0][:50]
            
        return info
    except:
        return None

async def download_movie_content(message_id):
    try:
        t_client = await get_client()
        message = await t_client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        file_name = f"movie_{int(time.time())}_{message_id}.mp4"
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        path = await t_client.download_media(message, file=file_path)
        if path:
            return {"url": f"{PUBLIC_URL}/files/{file_name}", "file_name": file_name}
        return None
    except Exception as e:
        print(f"Error descarga: {e}")
        return None

# --- APP FLASK ---
app = Flask(__name__)
CORS(app)

def run_async(coro):
    """Helper para ejecutar funciones async en Flask"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Falta parámetro q"}), 400
    results = run_async(search_movies_in_channel(query))
    return jsonify({"status": "success", "results": results, "count": len(results)})

@app.route("/download/<int:message_id>", methods=["GET"])
def download(message_id):
    result = run_async(download_movie_content(message_id))
    if not result:
        return jsonify({"error": "No se pudo descargar"}), 404
    return jsonify(result)

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

@app.route("/")
def index():
    return jsonify({"message": "Movie Search API Active"})

# Eliminado @app.before_first_request por incompatibilidad con Flask 2.3+

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
