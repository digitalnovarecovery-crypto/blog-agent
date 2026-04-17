from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
from ringcentral import SDK

import config

# Max audio file size for Claude transcription (25MB)
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Min call duration to bother transcribing (skip very short calls)
MIN_CALL_DURATION_SECS = 60

# Cache directory for downloaded recordings
RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "recordings"


class RingCentralClient:
    def __init__(self):
        self.sdk = SDK(config.RC_CLIENT_ID, config.RC_CLIENT_SECRET, config.RC_SERVER)
        self.platform = self.sdk.platform()
        self._claude = None

    def login(self):
        self.platform.login(jwt=config.RC_JWT_TOKEN)
        print("Logged into RingCentral.")

    def _get_claude(self) -> anthropic.Anthropic:
        if self._claude is None:
            self._claude = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return self._claude

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
        """Download a call recording. Returns (audio_bytes, content_type) or None."""
        recording = call_record.get("recording")
        if not recording:
            return None

        content_uri = recording.get("contentUri")
        if not content_uri:
            return None

        call_id = call_record.get("id", "unknown")

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
            print(f"  Error downloading recording for call {call_id}: {e}")
            return None

    def get_transcript(self, call_record: dict) -> str | None:
        """Transcribe a call recording using Claude's audio input capability.

        Downloads the recording from RingCentral and sends it to Claude
        for transcription. Much faster and more reliable than RC's async
        AI speech-to-text API (which requires special permissions).
        """
        call_id = call_record.get("id", "")

        result = self._download_recording(call_record)
        if not result:
            return None

        audio_data, content_type = result

        # Map RC content types to Claude-supported media types
        media_type_map = {
            "audio/mpeg": "audio/mpeg",
            "audio/mp3": "audio/mpeg",
            "audio/wav": "audio/wav",
            "audio/x-wav": "audio/wav",
            "audio/ogg": "audio/ogg",
            "audio/webm": "audio/webm",
        }
        media_type = media_type_map.get(content_type, "audio/mpeg")

        audio_b64 = base64.standard_b64encode(audio_data).decode("utf-8")

        try:
            client = self._get_claude()
            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=8000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "audio",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": audio_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Transcribe this phone call recording verbatim. "
                                "Label speakers as 'Staff:' and 'Caller:' based on context "
                                "(the staff member works at a recovery/treatment center). "
                                "Output ONLY the transcript text, no commentary."
                            ),
                        },
                    ],
                }],
            )
            transcript = response.content[0].text.strip()

            if len(transcript) < 50:
                print(f"  SKIP call {call_id}: transcript too short ({len(transcript)} chars)")
                return None

            print(f"  Transcribed call {call_id}: {len(transcript)} chars")
            return transcript

        except Exception as e:
            print(f"  Error transcribing call {call_id}: {e}")
            return None

    def get_calls_with_transcripts(self, days: int | None = None) -> list[dict]:
        """Fetch calls and transcribe them via Claude.

        Returns list of {call_id, timestamp, transcript, caller_info, to_number}.
        Includes to_number so the pipeline can route calls to the correct site.
        """
        calls = self.get_recent_calls(days)
        results = []

        for i, call in enumerate(calls):
            call_id = call.get("id", "")
            duration = call.get("duration", 0)
            print(f"\n  [{i+1}/{len(calls)}] Call {call_id} ({duration}s)...")

            transcript = self.get_transcript(call)
            if not transcript:
                continue

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

            # Brief pause between transcriptions to be respectful of API limits
            if i < len(calls) - 1:
                time.sleep(1)

        print(f"\nGot {len(results)} calls with transcripts out of {len(calls)} recordings.")
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
