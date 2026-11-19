"""Voice, image and video modality inference (issue #449)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom import AiSbomConfig, AiSbomExtractor
from nuguard.sbom.core.application_summary import infer_modalities_support
from nuguard.sbom.models import AiSbomDocument


def _infer(*files: tuple[str, str]) -> dict[str, bool]:
    return infer_modalities_support([], list(files))


def _extract(root: Path) -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(root, AiSbomConfig(enable_llm=False))


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (
            "openai_audio.py",
            "client = OpenAI()\nclient.audio.transcriptions.create(model='whisper-1', file=f)\n",
        ),
        (
            "azure_speech.py",
            "import azure.cognitiveservices.speech as speechsdk\n"
            "cfg = speechsdk.SpeechConfig(subscription=key, region=region)\n",
        ),
        (
            "realtime.ts",
            "const rt = client.beta.realtime;\nconst model = 'gpt-4o-realtime-preview';\n",
        ),
        ("tts.py", "import pyaudio\nimport pyttsx3\n"),
        (
            "mic.ts",
            "const ctx = new AudioContext();\nconst rec = new webkitSpeechRecognition();\n",
        ),
        (
            "eleven.py",
            "from elevenlabs.client import ElevenLabs\nclient = ElevenLabs(api_key=key)\n",
        ),
    ],
)
def test_voice_apis_in_python_and_typescript_are_detected(name: str, content: str) -> None:
    assert _infer((name, content))["voice"] is True


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("vision.py", "content = [{'type': 'image_url', 'image_url': {'url': url}}]\n"),
        ("gen.py", "client.images.generate(model='gpt-image-1', prompt=prompt)\n"),
        ("ocr.py", "import pytesseract\ntext = pytesseract.image_to_string(img)\n"),
        ("responses.ts", "const part = { type: 'input_image', image_url: url };\n"),
    ],
)
def test_image_apis_in_python_and_typescript_are_detected(name: str, content: str) -> None:
    assert _infer((name, content))["image"] is True


def test_video_capture_apis_are_detected() -> None:
    content = "cap = cv2.VideoCapture(0)\nout = cv2.VideoWriter('o.mp4', fourcc, 20.0, size)\n"
    assert _infer(("cam.py", content))["video"] is True


def test_a_single_match_is_not_enough() -> None:
    support = _infer(("sms.py", "import twilio\n"))
    assert support["text"] is True
    assert support["voice"] is False


def test_unrelated_words_do_not_trigger_modalities() -> None:
    support = _infer(("billing.py", "def send_invoice(invoice):\n    return invoice\n"))
    assert support == {"text": True, "voice": False, "image": False, "video": False}


def test_documentation_mentions_do_not_count() -> None:
    prose = "We use whisper and elevenlabs. Whisper handles transcription.\n"
    assert _infer(("README.md", prose))["voice"] is False
    assert _infer(("docs/guide.mdx", prose))["voice"] is False
    assert _infer(("app.py", prose))["voice"] is True


def test_lockfile_entries_do_not_count() -> None:
    lock = '"ffmpeg-static": "5.2.0",\n"ffmpeg-static": "5.2.0"\n'
    assert _infer(("package-lock.json", lock))["video"] is False
    assert _infer(("sub/yarn.lock", lock))["video"] is False
    assert _infer(("package.json", lock))["video"] is True


def test_signals_beyond_the_first_200_files_are_found() -> None:
    files = [(f"mod{i}.py", "x = 1\n") for i in range(250)]
    files.append(("late.py", "import pyaudio\nimport sounddevice\n"))
    assert infer_modalities_support([], files)["voice"] is True


def test_extractor_reports_voice_for_a_python_app(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "import azure.cognitiveservices.speech as speechsdk\n"
        "cfg = speechsdk.SpeechConfig(subscription=key, region=region)\n"
    )
    doc = _extract(tmp_path)
    assert doc.summary is not None
    assert doc.summary.modality_support["voice"] is True
    assert "VOICE" in doc.summary.modalities


def test_extractor_reports_voice_for_a_typescript_app(tmp_path: Path) -> None:
    (tmp_path / "voice.ts").write_text(
        "const ctx = new AudioContext();\nconst rec = new webkitSpeechRecognition();\n"
    )
    doc = _extract(tmp_path)
    assert doc.summary is not None
    assert doc.summary.modality_support["voice"] is True


def test_extractor_ignores_voice_words_that_only_appear_in_a_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("whisper elevenlabs whisper\n")
    (tmp_path / "app.py").write_text("print('hello')\n")
    doc = _extract(tmp_path)
    assert doc.summary is not None
    assert doc.summary.modality_support["voice"] is False
