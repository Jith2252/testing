"""Bounded FFmpeg jobs over a validated, range-aware HTTP relay.

FFmpeg never receives a remote URL or a local filename supplied by a user.
Only self-contained Matroska/WebM and MP4 containers are accepted.
"""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@contextmanager
def relay_source(url, open_target):
    """Validate every outbound request/redirect using the server's proxy policy."""
    key = "/" + secrets.token_urlsafe(24)
    deadline = time.monotonic() + 23
    remaining = [128 * 1024 * 1024]
    lock = threading.Lock()

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
                        break
                    chunk = response.read(size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except Exception:
                # Never disclose signed URLs or upstream errors to FFmpeg/client logs.
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
        yield f"http://127.0.0.1:{server.server_port}{key}"
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
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL)
            timer = threading.Timer(22, process.kill)
            timer.start()
            try:
                output = process.stdout.read(limit + 1)
                if len(output) > limit:
                    raise MediaError("Media output exceeded the processing limit.")
                process.wait(timeout=2)
                if process.returncode:
                    raise MediaError("Could not read this media track within the processing limit. The source must support HTTP byte ranges.")
                return output
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
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
            tracks.append({"index": int(stream["index"]), "kind": kind, "codec": codec,
                           "language": tags.get("language", ""), "label": tags.get("title") or tags.get("language") or f"{kind.title()} {len(tracks) + 1}",
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
    return run_job("ffmpeg", url, open_target, options, start=start), content_type
