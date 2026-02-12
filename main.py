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

# --- CONFIGURACIÓN DEL NUEVO CANAL ---
# Usamos el username directo del canal.
# NOTA: Tu cuenta (la de la SESSION_STRING) DEBE haberse unido al canal previamente.
CHANNEL_ID = 'peliculas_psicologicas'

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Configuración de Asyncio Global ---
# Loop global para evitar errores de "Event loop closed" en Fly.io
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# --- Cliente Telegram Global ---
if not API_ID or not API_HASH or not SESSION_STRING:
    raise Exception("❌ Error: Faltan las credenciales (API_ID, API_HASH, SESSION_STRING)")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

async def ensure_connection():
    """
    Asegura la conexión y resuelve la entidad del canal.
    """
    if not client.is_connected():
        print("🔄 Conectando a Telegram...")
        await client.connect()
    
    # Intentamos resolver el canal para que Telethon lo guarde en caché
    try:
        print(f"🔄 Verificando acceso al canal: {CHANNEL_ID}...")
        await client.get_input_entity(CHANNEL_ID)
        print("✅ Canal encontrado exitosamente.")
    except Exception as e:
        print(f"⚠️ Advertencia: No se pudo resolver '{CHANNEL_ID}' directamente.")
        print(f"Error: {e}")
        print("Intentando actualizar lista de diálogos...")
        # Si falla, leemos los diálogos para forzar la actualización de la base de datos local
        try:
            await client.get_dialogs(limit=50)
        except:
            pass

# --- Lógica de Búsqueda ---
async def search_movies_in_channel(search_query: str):
    try:
        await ensure_connection()
        
        search_query = search_query.lower().strip()
        results = []
        
        print(f"🔎 Buscando '{search_query}' en {CHANNEL_ID}...")
        
        # Aumenté el límite a 400 mensajes para tener más historial
        async for message in client.iter_messages(CHANNEL_ID, limit=400):
            if not message.text and not message.media:
                continue
            
            # Buscamos en el texto (caption) del mensaje
            message_text = (message.text or "").lower()
            
            # También verificamos el nombre del archivo si es un documento
            file_name = ""
            if message.file and message.file.name:
                file_name = message.file.name.lower()

            # Lógica de coincidencia: busca en el texto O en el nombre del archivo
            if search_query in message_text or search_query in file_name:
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
        
        return results
    except Exception as e:
        print(f"❌ Error buscando películas: {str(e)}")
        return []

def extract_movie_info(message):
    try:
        text = message.text or ""
        
        # Patrones regex para intentar sacar info estructurada
        patterns = {
            "title": r"(?:Título|Película|Movie|Nombre)[:\-]\s*(.+?)(?:\n|$)",
            "year": r"(?:Año|Year)[:\-]\s*(\d{4})",
            "quality": r"(?:Calidad|Quality)[:\-]\s*(.+?)(?:\n|$)",
            "size": r"(?:Tamaño|Size|Peso)[:\-]\s*(.+?)(?:\n|$)"
        }
        
        info = {"message_id": message.id, "text_preview": text[:100] + "..." if text else "Sin descripción"}
        
        # Intentar extraer datos con Regex
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[key] = match.group(1).strip()
        
        # --- ESTRATEGIAS DE RESPALDO (FALLBACKS) ---
        
        # 1. Si no hay título, usar el nombre del archivo
        if not info.get("title") and message.file and message.file.name:
            info["title"] = message.file.name
            
        # 2. Si aún no hay título, usar la primera línea del texto
        if not info.get("title") and text:
            first_line = text.split('\n')[0]
            info["title"] = first_line[:50]
            
        # 3. Si no hay nada, poner "Desconocido"
        if not info.get("title"):
            info["title"] = f"Película ID {message.id}"

        return info
    except:
        return None

async def download_movie_content(message_id):
    try:
        await ensure_connection()
        message = await client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        # Intentar obtener nombre original del archivo, si no, generar uno
        original_name = "video"
        ext = ".mp4"
        if message.file:
            if message.file.name:
                original_name = message.file.name
            if message.file.ext:
                ext = message.file.ext

        # Limpiamos el nombre de caracteres raros para evitar errores de sistema
        safe_name = re.sub(r'[\\/*?:"<>|]', "", original_name)
        file_name = f"{int(time.time())}_{safe_name}"
        if not file_name.endswith(ext):
            file_name += ext

        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        print(f"⬇️ Descargando: {file_name}...")
        path = await client.download_media(message, file=file_path)
        
        if path:
            print("✅ Descarga completada.")
            return {"url": f"{PUBLIC_URL}/files/{file_name}", "file_name": file_name}
        return None
    except Exception as e:
        print(f"❌ Error descarga: {e}")
        return None

# --- APP FLASK ---
app = Flask(__name__)
CORS(app)

def run_in_global_loop(coro):
    return loop.run_until_complete(coro)

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Falta parámetro q"}), 400
    
    results = run_in_global_loop(search_movies_in_channel(query))
    
    return jsonify({
        "status": "success", 
        "results": results, 
        "count": len(results)
    })

@app.route("/download/<int:message_id>", methods=["GET"])
def download(message_id):
    result = run_in_global_loop(download_movie_content(message_id))
    if not result:
        return jsonify({"error": "No se pudo descargar o mensaje sin media"}), 404
    return jsonify(result)

@app.route("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

@app.route("/")
def index():
    return jsonify({"message": f"API Activa buscando en: {CHANNEL_ID}"})

if __name__ == "__main__":
    # Intento de conexión inicial
    try:
        loop.run_until_complete(ensure_connection())
    except Exception as e:
        print(f"Error inicial: {e}")

    app.run(host="0.0.0.0", port=PORT)
