import os
import re
import asyncio
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import Channel

# --- 1. CONFIGURACIÓN ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://consulta-pe-bot.up.railway.app").rstrip("/")
SESSION_STRING = os.getenv("SESSION_STRING", None)
PORT = int(os.getenv("PORT", 8080))

# Nombre exacto del canal (username)
CHANNEL_USERNAME = 'peliculas_psicologicas'

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- 2. CLIENTE TELEGRAM ---
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

if not API_ID or not API_HASH or not SESSION_STRING:
    raise Exception("❌ Faltan credenciales API_ID, API_HASH o SESSION_STRING")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

# Variable global para guardar la entidad del canal y no buscarla siempre
cached_channel_entity = None

async def get_channel_entity():
    """
    Resuelve el canal y lo guarda en memoria. 
    Es vital para que Telethon sepa dónde buscar.
    """
    global cached_channel_entity
    
    if not client.is_connected():
        await client.connect()
    
    # Si ya lo tenemos en caché, retornamos rápido
    if cached_channel_entity:
        return cached_channel_entity

    try:
        print(f"🔄 Resolviendo entidad para: {CHANNEL_USERNAME}...")
        # Buscamos por nombre de usuario
        entity = await client.get_entity(CHANNEL_USERNAME)
        cached_channel_entity = entity
        print(f"✅ Canal localizado: {entity.title} (ID: {entity.id})")
        return entity
    except Exception as e:
        print(f"⚠️ Error resolviendo canal directamente: {e}")
        # Intento de emergencia: buscar en diálogos
        async for dialog in client.iter_dialogs(limit=50):
            if dialog.is_channel:
                # Comparamos username o título
                d_username = getattr(dialog.entity, 'username', '')
                if d_username and d_username.lower() == CHANNEL_USERNAME.lower():
                    cached_channel_entity = dialog.entity
                    print(f"✅ Canal encontrado en diálogos: {dialog.title}")
                    return dialog.entity
        return None

# --- 3. BÚSQUEDA ---
async def search_movies_in_channel(search_query: str):
    try:
        # 1. Obtener el canal seguro
        entity = await get_channel_entity()
        if not entity:
            print("❌ No se pudo encontrar el canal. Abortando búsqueda.")
            return []

        search_query = search_query.lower().strip()
        results = []
        
        print(f"🔎 ESCANEANDO CANAL: {entity.title} buscando '{search_query}'")
        
        # 2. Iterar mensajes (Límite 500 para velocidad)
        # Usamos 'entity' directamente, no el string
        count = 0
        async for message in client.iter_messages(entity, limit=500):
            count += 1
            if not message.text and not message.file:
                continue

            # Preparar textos para comparar
            text_content = (message.text or "").lower()
            file_name = ""
            if message.file and message.file.name:
                file_name = message.file.name.lower()
            
            # LOG DE DEPURACIÓN (Verás esto en fly logs si encuentra algo)
            # Solo imprimimos cada 50 mensajes para no saturar
            if count % 50 == 0:
                print(f"   ...Escaneado mensaje {count} (ID: {message.id})...")

            # 3. Comparación
            if search_query in text_content or search_query in file_name:
                print(f"   ✨ ¡COINCIDENCIA ENCONTRADA! ID: {message.id}")
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)

        print(f"✅ Fin del escaneo. Total revisados: {count}. Encontrados: {len(results)}")
        return results

    except Exception as e:
        print(f"❌ Error CRÍTICO en búsqueda: {str(e)}")
        # Si falla, limpiamos caché para forzar reconexión la próxima
        global cached_channel_entity
        cached_channel_entity = None
        return []

def extract_movie_info(message):
    try:
        text = message.text or ""
        info = {
            "message_id": message.id, 
            "text_preview": text[:100] + "..." if text else "Archivo multimedia"
        }

        # Intentar sacar título del nombre de archivo
        if message.file and message.file.name:
            clean = os.path.splitext(message.file.name)[0]
            clean = clean.replace(".", " ").replace("_", " ")
            info["title"] = clean
        
        # Si no, del texto
        if "title" not in info:
            first_line = text.split('\n')[0] if text else f"Video {message.id}"
            info["title"] = first_line[:50]
        
        # Tamaño
        if message.file:
            size_mb = message.file.size / (1024 * 1024)
            info["size"] = f"{size_mb:.2f} MB"
        
        return info
    except:
        return None

# --- 4. DESCARGA ---
async def download_movie_content(message_id):
    try:
        entity = await get_channel_entity()
        if not entity:
            return None

        message = await client.get_messages(entity, ids=message_id)
        if not message or not message.media:
            return None
        
        # Nombre de archivo
        original_name = "video"
        ext = ".mp4"
        if message.file:
            if message.file.name: original_name = message.file.name
            if message.file.ext: ext = message.file.ext

        safe_name = re.sub(r'[\\/*?:"<>|]', "", original_name).replace(" ", "_")
        file_name = f"{int(time.time())}_{safe_name}"
        if not file_name.endswith(ext): file_name += ext

        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        print(f"⬇️ Descargando: {file_name}")
        path = await client.download_media(message, file=file_path)
        
        if path:
            return {"url": f"{PUBLIC_URL}/files/{file_name}", "file_name": file_name}
        return None
    except Exception as e:
        print(f"❌ Error descarga: {e}")
        return None

# --- 5. SERVER ---
app = Flask(__name__)
CORS(app)

def run_async(coro):
    return loop.run_until_complete(coro)

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Falta parametro q"}), 400
    
    results = run_async(search_movies_in_channel(query))
    
    return jsonify({
        "status": "success", 
        "target": CHANNEL_USERNAME,
        "results": results, 
        "count": len(results)
    })

@app.route("/download/<int:message_id>", methods=["GET"])
def download(message_id):
    result = run_async(download_movie_content(message_id))
    if not result:
        return jsonify({"error": "Error descargando"}), 404
    return jsonify(result)

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/")
def index():
    return jsonify({"status": "API Online", "channel": CHANNEL_USERNAME})

if __name__ == "__main__":
    print("🚀 Iniciando Servidor...")
    try:
        loop.run_until_complete(get_channel_entity())
    except Exception as e:
        print(f"⚠️ Aviso inicio: {e}")
    
    app.run(host="0.0.0.0", port=PORT)
