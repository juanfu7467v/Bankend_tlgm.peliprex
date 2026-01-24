import os
import re
import asyncio
import json
import time
from datetime import datetime
from urllib.parse import unquote
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

# ID del canal de películas (obtenido de la URL: https://t.me/c/1507924325/2241)
CHANNEL_ID = -1001507924325  # El ID negativo es para canales/supergrupos

# --- Configuración Interna ---
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# --- Cliente Telegram Global ---
client = None

# --- Inicializar Cliente Telegram ---
async def init_telegram_client():
    global client
    if API_ID == 0 or not API_HASH or not SESSION_STRING:
        raise Exception("Credenciales de Telegram no configuradas.")
    
    session = StringSession(SESSION_STRING)
    client = TelegramClient(session, API_ID, API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        raise Exception("Cliente no autorizado.")
    
    return client

# --- Buscar Películas en el Canal ---
async def search_movies_in_channel(search_query: str):
    """
    Busca películas en el canal analizando los mensajes
    """
    try:
        if not client or not client.is_connected():
            await init_telegram_client()
        
        search_query = search_query.lower().strip()
        results = []
        
        # Obtener los últimos mensajes del canal
        messages = await client.get_messages(CHANNEL_ID, limit=100)
        
        for message in messages:
            if not message.text:
                continue
            
            message_text = message.text.lower()
            
            # Buscar coincidencias en el texto del mensaje
            if search_query in message_text:
                movie_info = extract_movie_info(message)
                if movie_info:
                    results.append(movie_info)
            
            # También buscar en hashtags o menciones
            if message.entities:
                for entity in message.entities:
                    if hasattr(entity, 'url') and search_query in entity.url.lower():
                        movie_info = extract_movie_info(message)
                        if movie_info:
                            results.append(movie_info)
        
        # Si no se encontraron resultados en los últimos 100 mensajes, buscar más profundamente
        if not results:
            messages = await client.get_messages(CHANNEL_ID, limit=300)
            
            for message in messages:
                if not message.text:
                    continue
                
                message_text = message.text.lower()
                
                # Buscar coincidencias parciales
                if any(word in message_text for word in search_query.split()):
                    movie_info = extract_movie_info(message)
                    if movie_info:
                        results.append(movie_info)
        
        return results
        
    except Exception as e:
        print(f"Error buscando películas: {str(e)}")
        return []

def extract_movie_info(message):
    """
    Extrae información de la película del mensaje
    """
    try:
        text = message.text
        
        # Patrones para extraer información de películas
        patterns = {
            "title": r"(?:Título|Película|Movie)[:\-]\s*(.+?)(?:\n|$)",
            "year": r"(?:Año|Year)[:\-]\s*(\d{4})",
            "quality": r"(?:Calidad|Quality)[:\-]\s*(.+?)(?:\n|$)",
            "format": r"(?:Formato|Format)[:\-]\s*(.+?)(?:\n|$)",
            "size": r"(?:Tamaño|Size)[:\-]\s*(.+?)(?:\n|$)",
            "language": r"(?:Idioma|Language)[:\-]\s*(.+?)(?:\n|$)",
            "subtitle": r"(?:Subtítulos|Subtitles)[:\-]\s*(.+?)(?:\n|$)"
        }
        
        info = {"message_id": message.id, "text_preview": text[:200] + "..." if len(text) > 200 else text}
        
        # Extraer información usando patrones
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                info[key] = match.group(1).strip()
        
        # Extraer enlaces de descarga
        download_links = extract_download_links(text)
        if download_links:
            info["download_links"] = download_links
        
        # Verificar si hay medios adjuntos
        if message.media:
            media_info = extract_media_info(message)
            if media_info:
                info["media"] = media_info
        
        # Extraer el título principal (primer línea o texto en negrita)
        if not info.get("title"):
            # Buscar texto en negrita o primera línea significativa
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if line and len(line) > 3 and not line.startswith(('http', 'https', '👤', '📅', '🔗')):
                    info["title"] = line
                    break
        
        return info if (info.get("title") or info.get("download_links")) else None
        
    except Exception as e:
        print(f"Error extrayendo información: {str(e)}")
        return None

def extract_download_links(text):
    """
    Extrae enlaces de descarga del texto
    """
    links = []
    
    # Patrones comunes de enlaces de descarga
    link_patterns = [
        r"(https?://[^\s]+)",
        r"(mega\.nz/[^\s]+)",
        r"(mediafire\.com/[^\s]+)",
        r"(drive\.google\.com/[^\s]+)",
        r"(terabox\.com/[^\s]+)",
        r"(1fichier\.com/[^\s]+)",
        r"(zippyshare\.com/[^\s]+)",
        r"(uploaded\.net/[^\s]+)",
        r"(rapidgator\.net/[^\s]+)",
        r"(nitroflare\.com/[^\s]+)"
    ]
    
    for pattern in link_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            if match.startswith("http"):
                links.append({"url": match, "type": "direct"})
            else:
                links.append({"url": f"https://{match}", "type": "direct"})
    
    return links if links else None

def extract_media_info(message):
    """
    Extrae información de medios adjuntos
    """
    try:
        if not message.media:
            return None
        
        media_info = {}
        
        # Obtener información del archivo
        if hasattr(message.media, 'document'):
            doc = message.media.document
            media_info["type"] = "document"
            media_info["size"] = doc.size
            media_info["mime_type"] = doc.mime_type
            
            # Extraer nombre de archivo
            for attr in doc.attributes:
                if hasattr(attr, 'file_name'):
                    media_info["file_name"] = attr.file_name
                    break
        
        elif hasattr(message.media, 'photo'):
            media_info["type"] = "photo"
        
        return media_info
        
    except:
        return None

async def download_movie_content(message_id):
    """
    Descarga el contenido multimedia del mensaje
    """
    try:
        if not client or not client.is_connected():
            await init_telegram_client()
        
        # Obtener el mensaje específico
        message = await client.get_messages(CHANNEL_ID, ids=message_id)
        
        if not message or not message.media:
            return None
        
        # Descargar el archivo
        timestamp = int(time.time())
        file_name = f"movie_{timestamp}_{message_id}"
        
        # Determinar extensión basada en el tipo de medio
        if hasattr(message.media, 'document'):
            doc = message.media.document
            ext = ".mp4"
            
            # Intentar determinar extensión por mime type
            if doc.mime_type:
                if "mp4" in doc.mime_type:
                    ext = ".mp4"
                elif "mkv" in doc.mime_type:
                    ext = ".mkv"
                elif "avi" in doc.mime_type:
                    ext = ".avi"
                elif "pdf" in doc.mime_type:
                    ext = ".pdf"
            
            file_name += ext
        
        file_path = os.path.join(DOWNLOAD_DIR, file_name)
        path = await client.download_media(message, file=file_path)
        
        if path:
            return {
                "url": f"{PUBLIC_URL}/files/{file_name}",
                "path": path,
                "file_name": file_name
            }
        
        return None
        
    except Exception as e:
        print(f"Error descargando contenido: {str(e)}")
        return None

# --- Funciones de Búsqueda Sincrónicas ---
def search_movies(query: str):
    """
    Función sincrónica para buscar películas
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(search_movies_in_channel(query))
    finally:
        loop.close()

def download_movie(message_id: int):
    """
    Función sincrónica para descargar película
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(download_movie_content(message_id))
    finally:
        loop.close()

# --- HELPER DE BÚSQUEDA ---
def get_search_params(path, request_args):
    """
    Obtiene parámetros de búsqueda basados en la ruta
    """
    query = request_args.get("q") or request_args.get("query") or request_args.get("search")
    
    # Mapeo de rutas a tipos de búsqueda
    mapping = {
        "movie": "película",
        "pelicula": "película",
        "film": "película",
        "series": "serie",
        "tv": "serie",
        "documentary": "documental"
    }
    
    search_type = mapping.get(path, "película")
    
    if not query:
        return None, "Consulta de búsqueda faltante"
    
    # Agregar tipo de búsqueda si es relevante
    full_query = f"{query} {search_type}"
    return full_query, None

# --- APP FLASK ---
app = Flask(__name__)
CORS(app)

@app.route("/files/<path:filename>")
def files(filename):
    return send_from_directory(DOWNLOAD_DIR, filename)

@app.route("/search", methods=["GET"])
def search_movies_endpoint():
    """
    Endpoint principal para buscar películas
    """
    query = request.args.get("q", "").strip()
    
    if not query:
        return jsonify({
            "status": "error",
            "message": "Se requiere un término de búsqueda (parámetro 'q')"
        }), 400
    
    results = search_movies(query)
    
    if not results:
        return jsonify({
            "status": "success",
            "message": "No se encontraron películas",
            "results": [],
            "count": 0
        })
    
    return jsonify({
        "status": "success",
        "message": f"Se encontraron {len(results)} resultados",
        "results": results,
        "count": len(results)
    })

@app.route("/download/<int:message_id>", methods=["GET"])
def download_movie_endpoint(message_id):
    """
    Endpoint para descargar película por ID de mensaje
    """
    try:
        result = download_movie(message_id)
        
        if not result:
            return jsonify({
                "status": "error",
                "message": "No se pudo descargar el contenido o no existe"
            }), 404
        
        return jsonify({
            "status": "success",
            "message": "Contenido disponible para descarga",
            "download_url": result["url"],
            "file_name": result["file_name"]
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/status", methods=["GET"])
def status_endpoint():
    return jsonify({
        "status": "online",
        "service": "Movie Search in Telegram Channel",
        "channel_id": CHANNEL_ID,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/health", methods=["GET"])
def health_endpoint():
    return jsonify({"status": "healthy"})

# --- Ruta Universal para Búsquedas Específicas ---
@app.route("/<path:endpoint>", methods=["GET"])
def universal_handler(endpoint):
    """
    Maneja rutas específicas para diferentes tipos de búsqueda
    """
    # Endpoints especiales
    if endpoint in ["files", "health", "status", "search", "download"]:
        return jsonify({"error": "Use la ruta específica"}), 404
    
    # Búsqueda universal
    query = request.args.get("q") or request.args.get("query") or endpoint.replace("_", " ")
    
    if not query:
        return jsonify({
            "status": "error",
            "message": "Se requiere un término de búsqueda"
        }), 400
    
    results = search_movies(query)
    
    return jsonify({
        "status": "success",
        "message": f"Búsqueda para '{query}'",
        "results": results,
        "count": len(results)
    })

# --- Inicialización al Arrancar ---
@app.before_first_request
def initialize():
    """
    Inicializa el cliente de Telegram al arrancar la aplicación
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(init_telegram_client())
        print("✅ Cliente Telegram inicializado correctamente")
    except Exception as e:
        print(f"❌ Error inicializando cliente Telegram: {str(e)}")

if __name__ == "__main__":
    # Inicializar cliente al iniciar
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(init_telegram_client())
        print("✅ Aplicación iniciada con cliente Telegram conectado")
    except Exception as e:
        print(f"⚠️  Aplicación iniciada sin cliente Telegram: {str(e)}")
    
    app.run(host="0.0.0.0", port=PORT)
