import json
import queue
import sys

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

import sounddevice as sd
from vosk import Model, KaldiRecognizer


class SpeechNode(Node):
    """Captures microphone audio and publishes recognized speech as text."""

    def __init__(self):
        super().__init__('speech_node')

        self.declare_parameter('model_path', 'model')
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('device', -1)

        model_path = self.get_parameter('model_path').value
        self.sample_rate = self.get_parameter('sample_rate').value
        self.device = self.get_parameter('device').value
        if self.device < 0:
            self.device = None  # use system default

        # Log available devices so the user can pick
        self.get_logger().info('Available input devices:')
        for i, dev in enumerate(sd.query_devices()):
            if dev['max_input_channels'] > 0:
                marker = ' (DEFAULT)' if i == sd.default.device[0] else ''
                self.get_logger().info(f'  [{i}] {dev["name"]}{marker}')

        chosen = sd.query_devices(self.device if self.device is not None else sd.default.device[0])
        self.get_logger().info(f'Using device: {chosen["name"]}')

        self.publisher = self.create_publisher(String, '/speech_text', 10)

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
        self.audio_queue = queue.Queue()

        self.get_logger().info('Speech node ready — speak into your microphone!')

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.get_logger().warn(f'Audio stream status: {status}')
        self.audio_queue.put(bytes(indata))

    def run(self):
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
                self._process_audio()

    def _process_audio(self):
        while not self.audio_queue.empty():
            data = self.audio_queue.get()
            if self.recognizer.AcceptWaveform(data):
                result = json.loads(self.recognizer.Result())
                text = result.get('text', '').strip()
                if text:
                    self.get_logger().info(f'Heard: "{text}"')
                    msg = String()
                    msg.data = text
                    self.publisher.publish(msg)


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
