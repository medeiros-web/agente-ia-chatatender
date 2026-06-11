"""
Transcrição de áudio via OpenAI Whisper API.
Suporta ogg/opus (WhatsApp), mp4, mp3, wav, webm.
Retorna texto ou None se transcrição não estiver disponível.
"""
import io
import json
import logging
import urllib.request
import urllib.error

import config

log = logging.getLogger(__name__)

# Mapa de extensão por MIME type
_MIME_EXT = {
    "audio/ogg":       "ogg",
    "audio/ogg; codecs=opus": "ogg",
    "audio/mpeg":      "mp3",
    "audio/mp4":       "mp4",
    "audio/mp4a-latm": "mp4",
    "audio/wav":       "wav",
    "audio/webm":      "webm",
    "video/mp4":       "mp4",
}


def is_available() -> bool:
    """Retorna True se transcrição está configurada e disponível."""
    return bool(config.AUDIO_TRANSCRIPTION and config.OPENAI_API_KEY)


def transcribe(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str | None:
    """
    Transcreve áudio usando OpenAI Whisper.
    Retorna o texto transcrito ou None em caso de falha.
    """
    if not is_available():
        return None
    if not audio_bytes:
        return None

    ext = _MIME_EXT.get(mime_type.lower().split(";")[0].strip(), "ogg")
    filename = f"audio.{ext}"

    # Monta multipart/form-data manualmente (sem dependências externas)
    boundary = "----WhisperBoundary7f3a9b"
    body = _build_multipart(boundary, audio_bytes, filename)

    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            text = result.get("text", "").strip()
            if text:
                log.info("Áudio transcrito (%d bytes) → %s chars", len(audio_bytes), len(text))
            return text or None
    except urllib.error.HTTPError as e:
        log.warning("Whisper HTTP %s: %s", e.code, e.read().decode(errors="replace")[:200])
    except Exception as e:
        log.warning("Falha na transcrição: %s", e)
    return None


def _build_multipart(boundary: str, audio: bytes, filename: str) -> bytes:
    """Constrói corpo multipart/form-data para a API Whisper."""
    sep = f"--{boundary}\r\n".encode()
    end = f"--{boundary}--\r\n".encode()

    # Campo: model
    model_part = (
        sep
        + b'Content-Disposition: form-data; name="model"\r\n\r\n'
        + b"whisper-1\r\n"
    )
    # Campo: language (forçar pt para melhor accuracy)
    lang_part = (
        sep
        + b'Content-Disposition: form-data; name="language"\r\n\r\n'
        + b"pt\r\n"
    )
    # Campo: response_format
    fmt_part = (
        sep
        + b'Content-Disposition: form-data; name="response_format"\r\n\r\n'
        + b"json\r\n"
    )
    # Campo: file
    file_disp = f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
    file_ct   = "Content-Type: application/octet-stream\r\n\r\n"
    file_part = sep + file_disp.encode() + file_ct.encode() + audio + b"\r\n"

    return model_part + lang_part + fmt_part + file_part + end
