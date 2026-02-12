import os
import re
import asyncio
import time
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

# --- CONFIGURACIÓN DEL CANAL ---
# Usamos el username público. Esto es lo más estable.
# El bot buscará en: t.me/peliculas_psicologicas (ALMACÉN PELIS FULL-HD)
CHANNEL_ID = 'peliculas_psicologicas'

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Configuración de Asyncio Global ---
# Mantenemos el loop global para evitar errores de "Event loop closed" en Fly.io
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# --- Cliente Telegram Global ---
if not API_ID or not API_HASH or not SESSION_STRING:
    raise Exception("❌ Error: Faltan las credenciales (API_ID, API_HASH, SESSION_STRING)")

# Inicializamos el cliente con el loop global
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

async def ensure_connection():
    """
    Asegura que la conexión esté viva y el canal esté reconocido.
    """
    if not client.is_connected():
        print("🔄 Conectando a Telegram...")
        await client.connect()
    
    # Verificación de acceso al canal
    try:
        # Intentamos 'ver' el canal usando su username
        await client.get_input_entity(CHANNEL_ID)
        # print(f"✅ Conectado exitosamente al canal: {CHANNEL_ID}")
    except Exception as e:
        print(f"⚠️ El canal '{CHANNEL_ID}' no está en caché. Actualizando diálogos...")
        try:
            # Si no lo encuentra, forzamos una actualización de la lista de chats
            await client.get_dialogs(limit=50)
        except Exception as inner_e:
            print(f"❌ Error crítico resolviendo el canal: {inner_e}")

# --- Lógica de Búsqueda ---
async def search_movies_in_channel(search_query: str):
    try:
        await ensure_connection()
        
        search_query = search_query.lower().strip()
        results = []
        
        print(f"🔎 Buscando '{search_query}' en {CHANNEL_ID}...")
        
        # Aumentamos el límite a 300 para buscar más atrás en el historial
        async for message in client.iter_messages(CHANNEL_ID, limit=300):
            # Ignorar mensajes de servicio o vacíos sin archivo
            if not message.text and not message.file:
                continue
            
            # 1. Buscar en el texto del mensaje
            text_content = (message.text or "").lower()
            match_text = search_query in text_content
            
            # 2. Buscar en el nombre del archivo (si existe)
            match_file = False
            if message.file and message.file.name:
                match_file = search_query in message.file.name.lower()
            
            # Si hay coincidencia en texto O archivo
            if match_text or match_file:
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
        
        return results
    except Exception as e:
        print(f"❌ Error en búsqueda: {str(e)}")
        return []

def extract_movie_info(message):
    try:
        text = message.text or ""
        
        # Diccionario para guardar la info
        info = {
            "message_id": message.id, 
            "text_preview": text[:100] + "..." if text else "Sin descripción"
        }

        # Intentar extraer info con Regex del texto
        patterns = {
            "title": r"(?:Título|Title|Película)[:\-]\s*(.+?)(?:\n|$)",
            "year": r"(?:Año|Year)[:\-]\s*(\d{4})",
            "quality": r"(?:Calidad|Quality)[:\-]\s*(.+?)(?:\n|$)",
            "size": r"(?:Tamaño|Peso|Size)[:\-]\s*(.+?)(?:\n|$)"
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[key] = match.group(1).strip()
        
        # --- ESTRATEGIAS DE RESPALDO (FALLBACKS) ---
        
        # A. Si no hay título en el texto, usar el nombre del archivo
        if not info.get("title") and message.file and message.file.name:
            # Limpiar extensión del nombre (ej: Batman.mp4 -> Batman)
            clean_name = os.path.splitext(message.file.name)[0]
            info["title"] = clean_name
            
        # B. Si aún no hay título, usar la primera línea del mensaje
        if not info.get("title") and text:
            info["title"] = text.split('\n')[0][:50]
            
        # C. Si falla todo, un nombre genérico
        if not info.get("title"):
            info["title"] = f"Video ID {message.id}"

        # D. Añadir metadatos extra si es un archivo
        if message.file:
            info["is_file"] = True
            info["filename"] = message.file.name
        
        return info
    except:
        return None

async def download_movie_content(message_id):
    try:
        await ensure_connection()
        message = await client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        # Determinar nombre del archivo
        original_name = "video"
        ext = ".mp4" # Extensión por defecto
        
        if message.file:
            if message.file.name:
                original_name = message.file.name
            if message.file.ext:
                ext = message.file.ext

        # Limpieza de nombre para evitar errores de sistema (ej: quitar / : *)
        safe_name = re.sub(r'[\\/*?:"<>|]', "", original_name)
        
        # Timestamp para evitar duplicados
        file_name = f"{int(time.time())}_{safe_name}"
        
        # Asegurar extensión
        if not file_name.lower().endswith(ext.lower()):
            file_name += ext

        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        print(f"⬇️ Iniciando descarga: {file_name}")
        path = await client.download_media(message, file=file_path)
        
        if path:
            print("✅ Descarga completada")
            return {
                "url": f"{PUBLIC_URL}/files/{file_name}", 
                "file_name": file_name,
                "size_bytes": os.path.getsize(file_path)
            }
        return None
    except Exception as e:
        print(f"❌ Error descarga: {e}")
        return None

# --- APP FLASK ---
app = Flask(__name__)
CORS(app)

def run_in_global_loop(coro):
    """Ejecuta corutinas en el loop global persistente"""
    return loop.run_until_complete(coro)

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Falta parámetro q"}), 400
    
    results = run_in_global_loop(search_movies_in_channel(query))
    
    return jsonify({
        "status": "success", 
        "channel": CHANNEL_ID,
        "results": results, 
        "count": len(results)
    })

@app.route("/download/<int:message_id>", methods=["GET"])
def download(message_id):
    result = run_in_global_loop(download_movie_content(message_id))
    if not result:
        return jsonify({"error": "No se pudo descargar"}), 404
    return jsonify(result)

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/health")
def health():
    return jsonify({
        "status": "ok", 
        "channel": CHANNEL_ID,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/")
def index():
    return jsonify({
        "message": "Movie Search API Ready", 
        "target_channel": f"t.me/{CHANNEL_ID}"
    })

if __name__ == "__main__":
    # Conexión inicial al arrancar la app
    try:
        print("🚀 Iniciando sistema...")
        loop.run_until_complete(ensure_connection())
    except Exception as e:
        print(f"⚠️ Error en conexión inicial (se reintentará en la petición): {e}")

    app.run(host="0.0.0.0", port=PORT)
