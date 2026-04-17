from __future__ import annotations

import io
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from ringcentral import SDK

import config

# Max audio file size for Whisper transcription (25MB)
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Whisper model — "tiny" is fastest, "base" is better quality, "small" best balance
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")

# Min call duration to bother transcribing (skip very short calls)
MIN_CALL_DURATION_SECS = 60

# Cache directory for downloaded recordings
RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"


class RingCentralClient:
    def __init__(self):
        self.sdk = SDK(config.RC_CLIENT_ID, config.RC_CLIENT_SECRET, config.RC_SERVER)
        self.platform = self.sdk.platform()
        self._whisper_model = None

    def _get_whisper(self):
        """Lazy-load the faster-whisper model."""
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            print(f"  Loading Whisper model '{WHISPER_MODEL_SIZE}' (first call only)...")
            self._whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type="int8",
            )
            print(f"  Whisper model loaded.")
        return self._whisper_model

    def login(self):
        self.platform.login(jwt=config.RC_JWT_TOKEN)
        print("Logged into RingCentral.")


    def _api_get(self, url_or_path, params=None, max_retries=3):
        """GET with automatic retry on 429 rate limit."""
        for attempt in range(max_retries):
            try:
                resp = self.platform.get(url_or_path, params) if params else self.platform.get(url_or_path)
                return resp
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate" in err_str.lower():
                    wait = 30 * (attempt + 1)
                    print(f"  Rate limited (429), waiting {wait}s before retry {attempt+1}/{max_retries}...")
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("Rate limit exceeded after retries")

    def get_recent_calls(self, days: int | None = None) -> list[dict]:
        """Fetch call log entries that have recordings from the past N days.

        Only returns inbound calls with duration >= MIN_CALL_DURATION_SECS,
        since short/outbound calls rarely contain useful blog material.
        """
        days = days or config.RC_CALL_LOG_DAYS
        date_from = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        all_records = []
        params = {
            "dateFrom": date_from,
            "recordingType": "All",
            "view": "Detailed",
            "perPage": 100,
        }

        resp = self._api_get("/account/~/call-log", params)
        data = resp.json_dict()
        all_records.extend(data.get("records", []))
        page = 1

        # Handle pagination with rate limit backoff
        while data.get("navigation", {}).get("nextPage"):
            page += 1
            time.sleep(1)
            next_uri = data["navigation"]["nextPage"]["uri"]
            try:
                resp = self._api_get(next_uri)
                data = resp.json_dict()
                all_records.extend(data.get("records", []))
            except RuntimeError:
                print(f"  Stopped at page {page} due to rate limiting. Got {len(all_records)} calls so far.")
                break

        # Filter: only calls with recordings, long enough to contain useful content
        calls = [
            r for r in all_records
            if r.get("recording")
            and r.get("duration", 0) >= MIN_CALL_DURATION_SECS
        ]

        print(f"Fetched {len(all_records)} total calls, {len(calls)} with recordings >= {MIN_CALL_DURATION_SECS}s (across {page} pages).")
        return calls

    def _download_recording(self, call_record: dict) -> tuple[bytes, str] | None:
        """Download a call recording with retry on rate limit. Returns (audio_bytes, content_type) or None."""
        recording = call_record.get("recording")
        if not recording:
            return None

        content_uri = recording.get("contentUri")
        if not content_uri:
            return None

        call_id = call_record.get("id", "unknown")

        for attempt in range(4):
            try:
                rec_resp = self.platform.get(content_uri)
                audio_data = rec_resp.response().content
                content_type = recording.get("contentType") or "audio/mpeg"

                if len(audio_data) > MAX_AUDIO_BYTES:
                    print(f"  SKIP call {call_id}: recording too large ({len(audio_data) / 1024 / 1024:.1f}MB)")
                    return None

                # Cache to disk for debugging/replay
                RECORDINGS_DIR.mkdir(exist_ok=True)
                ext = "mp3" if "mpeg" in content_type else "wav"
                cache_path = RECORDINGS_DIR / f"{call_id}.{ext}"
                if not cache_path.exists():
                    cache_path.write_bytes(audio_data)

                return audio_data, content_type

            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate" in err_str.lower():
                    wait = 60 * (attempt + 1)  # 60s, 120s, 180s
                    print(f"  Rate limited downloading {call_id}, waiting {wait}s (attempt {attempt+1}/4)...")
                    time.sleep(wait)
                    continue
                print(f"  Error downloading recording for call {call_id}: {e}")
                return None

        print(f"  SKIP call {call_id}: rate limit exceeded after retries")
        return None

    def get_transcript(self, call_record: dict) -> str | None:
        """Transcribe a call recording using local faster-whisper model.

        Downloads the recording from RingCentral and transcribes locally.
        No external API needed — runs on CPU with int8 quantization.
        """
        call_id = call_record.get("id", "")

        result = self._download_recording(call_record)
        if not result:
            return None

        audio_data, content_type = result

        # Determine file extension
        ext_map = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
            "audio/webm": "webm",
        }
        ext = ext_map.get(content_type, "mp3")

        try:
            model = self._get_whisper()

            # Write audio to temp file (faster-whisper needs a file path)
            with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name

            try:
                segments, info = model.transcribe(
                    tmp_path,
                    language="en",
                    initial_prompt="Phone call to a drug and alcohol recovery treatment center. Staff and callers discussing rehab, detox, sober living, insurance, and admissions.",
                    vad_filter=True,  # Skip silence for speed
                )
                transcript = " ".join(seg.text.strip() for seg in segments)
            finally:
                os.unlink(tmp_path)

            if len(transcript) < 50:
                print(f"  SKIP call {call_id}: transcript too short ({len(transcript)} chars)")
                return None

            print(f"  Transcribed call {call_id}: {len(transcript)} chars ({info.duration:.0f}s audio)")
            return transcript

        except Exception as e:
            print(f"  Error transcribing call {call_id}: {e}")
            return None

    def get_calls_with_transcripts(self, days: int | None = None,
                                     skip_call_ids: set[str] | None = None,
                                     max_transcriptions: int = 5) -> list[dict]:
        """Fetch calls and transcribe them via Claude.

        Returns list of {call_id, timestamp, transcript, caller_info, to_number}.
        Includes to_number so the pipeline can route calls to the correct site.
        skip_call_ids: set of call IDs to skip (already processed).
        max_transcriptions: cap on how many recordings to transcribe per run
                           (prevents rate-limit exhaustion and runaway API costs).
        """
        calls = self.get_recent_calls(days)
        skip_call_ids = skip_call_ids or set()

        # Pre-filter already-processed calls to avoid downloading/transcribing them
        new_calls = [c for c in calls if c.get("id", "") not in skip_call_ids]
        skipped = len(calls) - len(new_calls)
        if skipped:
            print(f"  Skipping {skipped} already-processed calls, {len(new_calls)} new to transcribe.")

        # Cap transcriptions per run to stay within rate limits
        if len(new_calls) > max_transcriptions:
            print(f"  Capping at {max_transcriptions} transcriptions this run (out of {len(new_calls)} available).")
            # Sort by most recent first so we process the latest calls first
            new_calls.sort(key=lambda c: c.get("startTime", ""), reverse=True)
            new_calls = new_calls[:max_transcriptions]

        results = []
        consecutive_rate_limits = 0

        for i, call in enumerate(new_calls):
            call_id = call.get("id", "")
            duration = call.get("duration", 0)
            print(f"\n  [{i+1}/{len(new_calls)}] Call {call_id} ({duration}s)...")

            transcript = self.get_transcript(call)
            if not transcript:
                # Check if we're hitting too many rate limits in a row
                consecutive_rate_limits += 1
                if consecutive_rate_limits >= 3:
                    print(f"\n  Stopping early: {consecutive_rate_limits} consecutive failures (likely rate limited).")
                    break
                continue
            else:
                consecutive_rate_limits = 0

            # Extract caller and destination info for site routing
            from_party = call.get("from", {})
            to_party = call.get("to", {})

            caller_info = ""
            if from_party.get("name"):
                caller_info = from_party["name"]
            elif from_party.get("phoneNumber"):
                caller_info = from_party["phoneNumber"]

            # to_number helps route calls to the correct site
            to_number = to_party.get("phoneNumber", "")

            results.append({
                "call_id": call_id,
                "timestamp": call.get("startTime", ""),
                "transcript": transcript,
                "caller_info": caller_info,
                "to_number": to_number,
                "duration": duration,
            })

            # Longer pause between transcriptions to respect RC rate limits
            if i < len(new_calls) - 1:
                time.sleep(3)

        print(f"\nGot {len(results)} calls with transcripts out of {len(new_calls)} attempted ({skipped} skipped as already processed).")
        return results

    def test_connection(self) -> bool:
        try:
            self.login()
            resp = self.platform.get("/account/~/extension/~")
            ext = resp.json_dict()
            print(f"RingCentral connected as: {ext.get('name', 'unknown')}")
            return True
        except Exception as e:
            print(f"RingCentral connection failed: {e}")
            return False
