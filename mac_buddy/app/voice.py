"""Talk instead of typing: open-source Whisper (whisper.cpp), running on this Mac.

Audio is recorded at 16 kHz mono while the mic button is held (or between two
clicks), then transcribed locally. Nothing leaves the Mac. The model (~148 MB)
is downloaded once, the first time voice is used, into
~/Library/Application Support/mac-buddy/models.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

MODELS_DIR = os.path.expanduser("~/Library/Application Support/mac-buddy/models")
MODEL = os.environ.get("MAC_BUDDY_WHISPER", "base")
RATE = 16000
# Whisper follows the vocabulary of its prompt: Mac app names, spelled the Mac way.
VOCAB = ("Mac Buddy, TextEdit, Finder, Stickies, Notes, Safari, Mail, Calendar, Reminders, Numbers, Pages, "
         "Keynote, Preview, Photos, Music, System Settings, Downloads, Desktop, Documents.")


class Voice:
    def __init__(self):
        self.stream = None
        self.chunks: list = []
        self.model = None
        self.lock = threading.Lock()

    @staticmethod
    def model_ready() -> bool:
        return os.path.exists(os.path.join(MODELS_DIR, f"ggml-{MODEL}.bin"))

    def start(self) -> str | None:
        """Start recording. Returns None, or a plain-language reason it couldn't."""
        try:
            import sounddevice as sd
        except ImportError:
            return "Voice isn't installed (sounddevice is missing)."
        self.chunks = []

        def cb(indata, frames, t, status):
            self.chunks.append(indata.copy())
        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=cb)
            self.stream.start()
        except Exception as e:
            self.stream = None
            return f"Couldn't use the microphone: {e}"
        return None

    def stop(self, on_text: Callable[[str], None], on_status: Callable[[str], None] | None = None):
        """Stop recording and transcribe on a background thread. Callbacks run on the main thread."""
        from PyObjCTools import AppHelper
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        chunks, self.chunks = self.chunks, []

        def work():
            import numpy as np
            if not chunks:
                AppHelper.callAfter(on_text, "")
                return
            audio = np.concatenate(chunks)[:, 0].astype(np.float32)
            if len(audio) < RATE * 0.3:  # under 0.3 s: nothing said
                AppHelper.callAfter(on_text, "")
                return
            try:
                with self.lock:
                    if self.model is None:
                        if on_status and not self.model_ready():
                            AppHelper.callAfter(on_status, "Getting the speech model ready (once, ~150 MB)…")
                        from pywhispercpp.model import Model
                        os.makedirs(MODELS_DIR, exist_ok=True)
                        self.model = Model(MODEL, models_dir=MODELS_DIR, redirect_whispercpp_logs_to=None,
                                           print_progress=False, print_realtime=False)
                    segs = self.model.transcribe(audio, initial_prompt=VOCAB)
                text = " ".join(s.text.strip() for s in segs).strip()
                # whisper marks silence and noise like "[BLANK_AUDIO]" or "(music)"
                import re
                text = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text).strip()
            except Exception as e:
                text = ""
                if on_status:
                    AppHelper.callAfter(on_status, f"Couldn't understand the audio: {e}")
            AppHelper.callAfter(on_text, text)
        threading.Thread(target=work, daemon=True).start()
