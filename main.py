import os
import re
import asyncio
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- 1. CONFIGURACIÓN Y VARIABLES ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://consulta-pe-bot.up.railway.app").rstrip("/")
SESSION_STRING = os.getenv("SESSION_STRING", None)
PORT = int(os.getenv("PORT", 8080))

# CANAL OBJETIVO:
CHANNEL_ID = 'peliculas_psicologicas'

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- 2. MOTOR DE TELEGRAM (ASYNCIO) ---
# Creamos un loop global para evitar conflictos con Gunicorn/Fly.io
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

if not API_ID or not API_HASH or not SESSION_STRING:
    raise Exception("❌ Error Crítico: Faltan las credenciales (API_ID, API_HASH, SESSION_STRING)")

# Inicializamos el cliente
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

async def ensure_connection():
    """
    Garantiza que la conexión con Telegram esté activa y el canal esté accesible.
    """
    if not client.is_connected():
        print("🔌 Conectando a los servidores de Telegram...")
        await client.connect()
    
    # Pequeña pausa para estabilizar la conexión en entornos cloud (Fly.io)
    await asyncio.sleep(1)

    try:
        # Intentamos 'tocar' el canal para asegurar que Telethon lo tiene en caché
        await client.get_input_entity(CHANNEL_ID)
    except Exception as e:
        print(f"⚠️ Aviso: Resolviendo entidad del canal '{CHANNEL_ID}'... ({e})")
        try:
            await client.get_dialogs(limit=20)
        except:
            pass

# --- 3. LÓGICA DE BÚSQUEDA ROBUSTA ---
async def search_movies_in_channel(search_query: str):
    try:
        await ensure_connection()
        
        # Limpieza de la consulta
        query = search_query.lower().strip()
        results = []
        
        print(f"🚀 Iniciando búsqueda profunda de: '{query}' en {CHANNEL_ID}")
        
        # LÍMITE AUMENTADO A 1500: Para encontrar películas más antiguas
        count_scanned = 0
        async for message in client.iter_messages(CHANNEL_ID, limit=1500):
            count_scanned += 1
            
            # Ignorar mensajes irrelevantes (sin texto y sin archivo)
            if not message.text and not message.file:
                continue
            
            # Variables de comparación
            text_content = (message.text or "").lower()
            file_name = message.file.name.lower() if (message.file and message.file.name) else ""
            
            # LÓGICA DE COINCIDENCIA (Texto O Nombre de archivo)
            match_found = (query in text_content) or (query in file_name)
            
            if match_found:
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
        
        print(f"✅ Búsqueda finalizada. Escaneados: {count_scanned}. Encontrados: {len(results)}")
        return results
        
    except Exception as e:
        print(f"❌ Error fatal en búsqueda: {str(e)}")
        # Intento de reconexión de emergencia para la próxima
        try:
            await client.disconnect()
            await client.connect()
        except:
            pass
        return []

def extract_movie_info(message):
    """Extrae datos bonitos del mensaje o usa valores por defecto"""
    try:
        text = message.text or ""
        
        info = {
            "message_id": message.id, 
            "text_preview": text[:150] + "..." if len(text) > 150 else text
        }

        # Intentar sacar título limpio del archivo primero
        if message.file and message.file.name:
            # Elimina extensión (.mp4) y caracteres raros (., _, -) para mostrarlo bonito
            clean_name = os.path.splitext(message.file.name)[0]
            clean_name = clean_name.replace(".", " ").replace("_", " ").replace("-", " ")
            info["title"] = clean_name
        
        # Si no hay archivo, intentar Regex en el texto
        if "title" not in info:
            match = re.search(r"(?:Título|Movie)[:\-]\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
            if match:
                info["title"] = match.group(1).strip()
            else:
                # Fallback final: Primera línea del texto
                info["title"] = text.split('\n')[0][:60] if text else f"Video {message.id}"

        # Datos extra
        info["size"] = f"{message.file.size / 1024 / 1024:.1f} MB" if message.file else "N/A"
        
        return info
    except:
        return None

# --- 4. LÓGICA DE DESCARGA ---
async def download_movie_content(message_id):
    try:
        await ensure_connection()
        message = await client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        # Definir nombre del archivo
        original_name = "video_download"
        ext = ".mp4"
        
        if message.file:
            if message.file.name:
                original_name = message.file.name
            if message.file.ext:
                ext = message.file.ext

        # Sanitizar nombre (Quitar caracteres prohibidos en Linux/Windows)
        safe_name = re.sub(r'[\\/*?:"<>|]', "", original_name)
        safe_name = safe_name.replace(" ", "_") # Espacios a guiones bajos para URL segura
        
        # Timestamp para que sea único
        file_name = f"{int(time.time())}_{safe_name}"
        if not file_name.endswith(ext):
            file_name += ext

        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        print(f"⬇️ Descargando archivo: {file_name}...")
        path = await client.download_media(message, file=file_path)
        
        if path:
            print("✅ Descarga exitosa.")
            return {
                "url": f"{PUBLIC_URL}/files/{file_name}", 
                "file_name": file_name,
                "size_mb": f"{os.path.getsize(file_path)/1024/1024:.2f}"
            }
        return None
    except Exception as e:
        print(f"❌ Error en descarga: {e}")
        return None

# --- 5. SERVIDOR FLASK ---
app = Flask(__name__)
CORS(app)

# Helper para ejecutar funciones async dentro de Flask
def run_async(coro):
    return loop.run_until_complete(coro)

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Debes enviar el parámetro 'q'. Ejemplo: /search?q=batman"}), 400
    
    results = run_async(search_movies_in_channel(query))
    
    return jsonify({
        "status": "success", 
        "query": query,
        "count": len(results),
        "results": results
    })

@app.route("/download/<int:message_id>", methods=["GET"])
def download(message_id):
    result = run_async(download_movie_content(message_id))
    if not result:
        return jsonify({"error": "No se pudo descargar. Verifica que el ID sea correcto."}), 404
    return jsonify(result)

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@app.route("/")
def index():
    return jsonify({
        "msg": "API de Películas Activa 🟢", 
        "channel": CHANNEL_ID,
        "instructions": "Usa /search?q=nombre para buscar."
    })

if __name__ == "__main__":
    print("🚀 Arrancando Servidor...")
    # Intento de conexión inicial
    try:
        loop.run_until_complete(ensure_connection())
    except Exception as e:
        print(f"⚠️ La conexión inicial falló (se reintentará en la búsqueda): {e}")

    app.run(host="0.0.0.0", port=PORT)
