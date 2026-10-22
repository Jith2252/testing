"""Bounded FFmpeg jobs over a validated, range-aware HTTP relay.

FFmpeg never receives a remote URL or a local filename supplied by a user.
Only self-contained Matroska/WebM and MP4 containers are accepted.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time

TEXT_CODECS = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
CHUNK_SECONDS = 30
_jobs = threading.BoundedSemaphore(2)


class MediaError(Exception):
    pass


class RelayedURL(str):
    """A local URL with bounded upstream failure diagnostics attached."""
    pass


def processing_error(stderr, *, timed_out=False):
    """Return useful errors without echoing source URLs or arbitrary metadata."""
    detail = stderr.lower()
    if timed_out:
        return "Media processing exceeded 22 seconds. Retry or use a source with faster indexed seeking."
    if "not on whitelist" in detail or "not on the whitelist" in detail:
        return "FFmpeg rejected the container or protocol under the server's processing policy."
    if "unrecognized option" in detail or "option not found" in detail:
        return "The installed FFmpeg version does not support a required processing option."
    if "error while loading shared libraries" in detail:
        return "FFmpeg runtime libraries are missing. Check the server's FFmpeg installation."
    if "decoder" in detail or "encoder" in detail:
        return "The installed FFmpeg build cannot decode or convert this track's codec."
    if "invalid data" in detail or "ebml header" in detail:
        return "FFmpeg could not parse the media container returned by the source."
    return "FFmpeg could not inspect or extract this media. Check server logs for the processing diagnostic."


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@contextmanager
def relay_source(url, open_target):
    """Validate every outbound request/redirect using the server's proxy policy."""
    key = "/" + secrets.token_urlsafe(24)
    deadline = time.monotonic() + 23
    remaining = [128 * 1024 * 1024]
    lock = threading.Lock()
    failure = {}

    class Relay(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != key or time.monotonic() > deadline:
                self.send_error(404)
                return
            response = None
            try:
                headers = {"Accept-Encoding": "identity"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                response = open_target(url, headers, 8)
                self.send_response(response.status)
                for name in ("Content-Length", "Content-Range", "Accept-Ranges", "Content-Type"):
                    if response.headers.get(name):
                        self.send_header(name, response.headers[name])
                self.end_headers()
                while time.monotonic() < deadline:
                    with lock:
                        size = min(65536, remaining[0])
                        remaining[0] -= size
                    if size <= 0:
                        failure["message"] = "Media processing exceeded the 128 MiB source-read limit."
                        break
                    chunk = response.read(size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # FFmpeg routinely stops reading after probing/decoding enough data.
                self.close_connection = True
            except Exception as error:
                status = getattr(error, "status", None) or getattr(error, "code", None)
                failure["message"] = (f"The media source could not be read by the server (HTTP {int(status)})."
                                      if isinstance(status, int) else
                                      "The server lost its connection to the media source. Retry inspection or extraction.")
                logging.getLogger(__name__).warning("Media relay failed: %s (status %s)", type(error).__name__, status)
                self.close_connection = True
            finally:
                if response is not None:
                    response.close()
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = RelayedURL(f"http://127.0.0.1:{server.server_port}{key}")
        source.failure = failure
        yield source
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def run_job(binary, url, open_target, output_args, *, start=None, limit=4 * 1024 * 1024):
    if not available():
        raise MediaError("Install FFmpeg and FFprobe on the server to read embedded MKV tracks.")
    if not _jobs.acquire(blocking=False):
        raise MediaError("Media processing is busy. Try again shortly.")
    try:
        with relay_source(url, open_target) as source:
            args = [binary, "-v", "error", "-protocol_whitelist", "http,tcp",
                    "-format_whitelist", "matroska,webm,mov", "-probesize", "8000000",
                    "-analyzeduration", "5000000", "-rw_timeout", "8000000"]
            if start is not None:
                args += ["-ss", str(start)]
            args += ["-i", source] + output_args
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       stdin=subprocess.DEVNULL)
            diagnostics = bytearray()
            timed_out = threading.Event()
            def read_errors():
                while True:
                    chunk = process.stderr.read(1024)
                    if not chunk:
                        break
                    diagnostics.extend(chunk[:max(0, 8192 - len(diagnostics))])
            reader = threading.Thread(target=read_errors, daemon=True)
            reader.start()
            def expire():
                timed_out.set()
                if process.poll() is None:
                    process.kill()
            timer = threading.Timer(22, expire)
            timer.start()
            try:
                output = process.stdout.read(limit + 1)
                if len(output) > limit:
                    raise MediaError("Media output exceeded the processing limit.")
                process.wait(timeout=2)
                reader.join(timeout=1)
                detail = diagnostics.decode("utf-8", errors="replace")
                safe_detail = re.sub(r"https?://[^\s\]\"']+", "[media source]", detail)
                safe_detail = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", safe_detail)
                logging.getLogger(__name__).debug("%s output %s bytes: %s", binary, len(output), safe_detail[:4096])
                if source.failure.get("message"):
                    raise MediaError(source.failure["message"])
                if process.returncode:
                    logging.getLogger(__name__).warning("%s failed (exit %s): %s", binary, process.returncode, safe_detail[:2048])
                    raise MediaError(processing_error(detail, timed_out=timed_out.is_set()))
                return output
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                reader.join(timeout=1)
                process.stdout.close()
                process.stderr.close()
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MediaError("Media processing failed or timed out.") from error
    finally:
        _jobs.release()


def inspect(url, open_target):
    raw = run_job("ffprobe", url, open_target,
                  ["-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=default,forced",
                   "-of", "json"])
    try:
        streams = json.loads(raw)["streams"]
        tracks = []
        for stream in streams[:64]:
            kind = stream.get("codec_type")
            if kind not in {"audio", "subtitle"}:
                continue
            tags = stream.get("tags", {})
            disposition = stream.get("disposition", {})
            codec = stream.get("codec_name", "unknown")
            name = tags.get("title") or f"{kind.title()} {stream['index']}"
            description = ", ".join(value for value in (tags.get("language", ""), codec) if value)
            tracks.append({"index": int(stream["index"]), "kind": kind, "codec": codec,
                           "language": tags.get("language", ""), "label": f"{name} ({description})",
                           "default": bool(disposition.get("default")), "forced": bool(disposition.get("forced")),
                           "supported": kind == "audio" or codec in TEXT_CODECS})
        return tracks
    except (ValueError, KeyError, TypeError) as error:
        raise MediaError("The media inspection returned invalid track information.") from error


def extract(url, track, start, open_target):
    if track["kind"] == "subtitle":
        if not track["supported"]:
            raise MediaError("Image-based subtitles require OCR; upload an SRT or WebVTT file instead.")
        options = ["-t", str(CHUNK_SECONDS), "-map", f"0:{track['index']}", "-c:s", "webvtt", "-f", "webvtt", "pipe:1"]
        content_type = "text/vtt; charset=utf-8"
    else:
        options = ["-t", str(CHUNK_SECONDS), "-map", f"0:{track['index']}", "-vn", "-sn", "-ac", "2", "-ar", "24000",
                   "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"]
        content_type = "audio/wav"
    body = run_job("ffmpeg", url, open_target, options, start=start)
    if track["kind"] == "audio" and len(body) < 1024:
        raise MediaError("No audio samples were returned at this position. Retry or select another audio track.")
    return body, content_type
