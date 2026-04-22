import json
import io
import os
import queue
import threading
import wave

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

import sounddevice as sd
from openai import OpenAI
from vosk import Model, KaldiRecognizer


class SpeechNode(Node):
    """Captures microphone audio and publishes recognized speech as text."""

    def __init__(self):
        super().__init__('speech_node')

        self.declare_parameter('speech_backend', 'vosk')
        self.declare_parameter('model_path', 'model')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('device_name', 'Microphone (Realtek(R) Audio)')
        self.declare_parameter('openai_api_key', '')
        self.declare_parameter('openai_stt_model', 'gpt-4o-mini-transcribe')
        self.declare_parameter('openai_record_seconds', 4.0)

        self.speech_backend = self.get_parameter('speech_backend').value
        model_path = self.get_parameter('model_path').value
        self.sample_rate = self.get_parameter('sample_rate').value
        device_name = self.get_parameter('device_name').value
        self.openai_stt_model = self.get_parameter('openai_stt_model').value
        self.openai_record_seconds = float(self.get_parameter('openai_record_seconds').value)

        # Resolve device name → index (stable across device renumbering)
        self.device = self._find_device_by_name(device_name)

        # Log available devices so the user can see options
        self.get_logger().info('Available input devices:')
        for i, dev in enumerate(sd.query_devices()):
            if dev['max_input_channels'] > 0:
                marker = ' ← selected' if i == self.device else ''
                self.get_logger().info(f'  [{i}] {dev["name"]}{marker}')

        chosen = sd.query_devices(self.device)
        self.get_logger().info(f'Using device [{self.device}]: {chosen["name"]}')

        self.publisher = self.create_publisher(String, '/speech_text', 10)
        self.audio_queue = queue.Queue()
        self._record_event = threading.Event()
        self._openai_client = None

        if self.speech_backend == 'vosk':
            self.get_logger().info(f'Loading Vosk model from: {model_path}')
            try:
                self.model = Model(model_path)
            except Exception as e:
                self.get_logger().error(
                    f'Failed to load Vosk model from "{model_path}". '
                    'Download a model from https://alphacephei.com/vosk/models '
                    'and extract it to a folder named "model" in the project root.'
                )
                raise SystemExit(1) from e

            self.recognizer = KaldiRecognizer(self.model, self.sample_rate)
            self.get_logger().info('Speech node ready with VOSK backend.')
        elif self.speech_backend == 'openai':
            api_key = self.get_parameter('openai_api_key').value or os.getenv('OPENAI_API_KEY', '')
            if not api_key:
                self.get_logger().error(
                    'OpenAI backend selected but no API key found. '
                    'Set OPENAI_API_KEY or pass --ros-args -p openai_api_key:=...'
                )
                raise SystemExit(1)

            self._openai_client = OpenAI(api_key=api_key)
            threading.Thread(target=self._ptt_input_loop, daemon=True).start()
            self.get_logger().info(
                'Speech node ready with OpenAI backend. Press Enter to record audio.'
            )
        else:
            self.get_logger().error(
                f'Unsupported speech_backend="{self.speech_backend}". Use "vosk" or "openai".'
            )
            raise SystemExit(1)

    def _ptt_input_loop(self):
        while rclpy.ok():
            try:
                input('Press Enter to record command...')
            except EOFError:
                break
            self._record_event.set()

    def _find_device_by_name(self, name: str) -> int:
        """Find the first input device whose name contains the given string (case-insensitive)."""
        name_lower = name.lower()
        for i, dev in enumerate(sd.query_devices()):
            if dev['max_input_channels'] > 0 and name_lower in dev['name'].lower():
                return i
        self.get_logger().error(
            f'No input device matching "{name}" found — falling back to system default. '
            'Check available devices in the log above.'
        )
        return sd.default.device[0]

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.get_logger().warn(f'Audio stream status: {status}')
        self.audio_queue.put(bytes(indata))

    def run(self):
        if self.speech_backend == 'vosk':
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=8000,
                dtype='int16',
                channels=1,
                device=self.device,
                callback=self._audio_callback,
            ):
                while rclpy.ok():
                    rclpy.spin_once(self, timeout_sec=0.05)
                    self._process_vosk_audio()
            return

        while rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)
            if not self._record_event.is_set():
                continue

            self._record_event.clear()
            text = self._capture_and_transcribe_openai()
            if text:
                self._publish_text(text)

    def _publish_text(self, text: str):
        clean_text = text.strip()
        if not clean_text:
            return

        self.get_logger().info(f'Heard: "{clean_text}"')
        msg = String()
        msg.data = clean_text
        self.publisher.publish(msg)

    def _process_vosk_audio(self):
        while not self.audio_queue.empty():
            data = self.audio_queue.get()
            if self.recognizer.AcceptWaveform(data):
                result = json.loads(self.recognizer.Result())
                text = result.get('text', '').strip()
                self._publish_text(text)

    def _capture_and_transcribe_openai(self) -> str:
        self.get_logger().info(
            f'Recording for {self.openai_record_seconds:.1f}s. Speak now...'
        )
        recording = sd.rec(
            int(self.openai_record_seconds * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1,
            dtype='int16',
            device=self.device,
        )
        sd.wait()

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(recording.tobytes())

        wav_buffer.seek(0)
        wav_buffer.name = 'speech.wav'
        try:
            transcript = self._openai_client.audio.transcriptions.create(
                model=self.openai_stt_model,
                file=wav_buffer,
            )
        except Exception as exc:
            self.get_logger().error(f'OpenAI transcription failed: {exc}')
            return ''

        return getattr(transcript, 'text', '').strip()


def main(args=None):
    rclpy.init(args=args)
    node = SpeechNode()
    try:
        node.run()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
