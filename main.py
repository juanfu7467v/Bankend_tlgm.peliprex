import os
import re
import asyncio
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import ChannelPrivateError

# --- Configuración y Variables de Entorno ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://consulta-pe-bot.up.railway.app").rstrip("/")
SESSION_STRING = os.getenv("SESSION_STRING", None)
PORT = int(os.getenv("PORT", 8080))

# ID del canal (Asegúrate que este ID sea correcto y el bot esté en el canal)
# Si sigue fallando el ID, intenta poner el Username (ej: 'peliprex_canal') si es público.
CHANNEL_ID = -1001507924325 

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Configuración de Asyncio Global ---
# Creamos un loop global para evitar el error "Event loop is closed"
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# --- Cliente Telegram Global ---
# Inicializamos el cliente UNA sola vez con el loop global
if not API_ID or not API_HASH or not SESSION_STRING:
    raise Exception("❌ Error: Faltan las credenciales (API_ID, API_HASH, SESSION_STRING)")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, loop=loop)

async def ensure_connection():
    """
    Verifica la conexión y 'refresca' el conocimiento del canal.
    Esto soluciona el error 'Could not find the input entity'.
    """
    if not client.is_connected():
        print("🔄 Conectando a Telegram...")
        await client.connect()
    
    # Intentamos obtener la entidad del canal para cachearla
    try:
        # Esto obliga a Telethon a buscar y guardar los datos del canal
        await client.get_input_entity(CHANNEL_ID)
    except Exception as e:
        print(f"⚠️ Advertencia: No se pudo resolver el canal {CHANNEL_ID}. Error: {e}")
        # Intento secundario: leer diálogos recientes para encontrar el canal
        try:
            await client.get_dialogs(limit=20)
        except:
            pass

# --- Lógica de Búsqueda ---
async def search_movies_in_channel(search_query: str):
    try:
        await ensure_connection()
        
        search_query = search_query.lower().strip()
        results = []
        
        print(f"🔎 Buscando '{search_query}' en el canal {CHANNEL_ID}...")
        
        # Buscar en los últimos 200 mensajes
        async for message in client.iter_messages(CHANNEL_ID, limit=200):
            if not message.text:
                continue
            
            message_text = message.text.lower()
            # Búsqueda simple: coincidencia exacta o palabras clave
            if search_query in message_text:
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
        
        return results
    except Exception as e:
        print(f"❌ Error buscando películas: {str(e)}")
        return []

def extract_movie_info(message):
    try:
        text = message.text
        # Patrones regex mejorados
        patterns = {
            "title": r"(?:Título|Película|Movie)[:\-]\s*(.+?)(?:\n|$)",
            "year": r"(?:Año|Year)[:\-]\s*(\d{4})",
            "quality": r"(?:Calidad|Quality)[:\-]\s*(.+?)(?:\n|$)",
            "size": r"(?:Tamaño|Size)[:\-]\s*(.+?)(?:\n|$)"
        }
        
        info = {"message_id": message.id, "text_preview": text[:100] + "..."}
        
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[key] = match.group(1).strip()
        
        # Si no encontró título por regex, usa la primera línea
        if not info.get("title"):
            first_line = text.split('\n')[0]
            info["title"] = first_line[:50]
            
        return info
    except:
        return None

async def download_movie_content(message_id):
    try:
        await ensure_connection()
        message = await client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        file_name = f"movie_{int(time.time())}_{message_id}.mp4"
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        
        # Descarga el archivo
        path = await client.download_media(message, file=file_path)
        if path:
            return {"url": f"{PUBLIC_URL}/files/{file_name}", "file_name": file_name}
        return None
    except Exception as e:
        print(f"❌ Error descarga: {e}")
        return None

# --- APP FLASK ---
app = Flask(__name__)
CORS(app)

def run_in_global_loop(coro):
    """
    Ejecuta una corutina en el loop global de Asyncio.
    Esto evita crear y cerrar loops constantemente.
    """
    return loop.run_until_complete(coro)

@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Falta parámetro q"}), 400
    
    # Ejecutamos la búsqueda usando el helper del loop global
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
    return jsonify({"message": "Movie Search API Active (Telethon Fixed)"})

if __name__ == "__main__":
    # Aseguramos conexión al iniciar (opcional, pero recomendado)
    try:
        loop.run_until_complete(ensure_connection())
    except Exception as e:
        print(f"Error inicial de conexión: {e}")

    app.run(host="0.0.0.0", port=PORT)
