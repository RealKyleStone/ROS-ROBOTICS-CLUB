import io
import json
import os
import wave

import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist
import sounddevice as sd
from openai import OpenAI

# Keyword → (linear.x, angular.z)
COMMANDS = {
    'forward':  ( 2.0,  0.0),
    'backward': (-2.0,  0.0),
    'back':     (-2.0,  0.0),
    'left':     ( 0.0,  2.0),
    'right':    ( 0.0, -2.0),
    'stop':     ( 0.0,  0.0),
    'go':       ( 2.0,  0.0),
    'reverse':  (-2.0,  0.0),
}


class CommandNode(Node):
    """Subscribes to /speech_text and publishes Twist commands to /turtle1/cmd_vel."""

    def __init__(self):
        super().__init__('command_node')

        self.declare_parameter('cmd_vel_topic', '/turtle1/cmd_vel')
        self.declare_parameter('command_backend', 'keyword')
        self.declare_parameter('openai_api_key', '')
        self.declare_parameter('openai_model', 'gpt-4o-mini')
        self.declare_parameter('tts_model', 'gpt-4o-mini-tts')
        self.declare_parameter('tts_voice', 'alloy')
        self.declare_parameter('speak_responses', True)

        topic = self.get_parameter('cmd_vel_topic').value
        self.command_backend = self.get_parameter('command_backend').value
        self.openai_model = self.get_parameter('openai_model').value
        self.tts_model = self.get_parameter('tts_model').value
        self.tts_voice = self.get_parameter('tts_voice').value
        self.speak_responses = bool(self.get_parameter('speak_responses').value)

        self.publisher = self.create_publisher(Twist, topic, 10)
        self.subscription = self.create_subscription(
            String, '/speech_text', self._on_speech, 10
        )

        # Timer for continuous movement (e.g. "go" command)
        self._continuous_twist = None
        self._timer = self.create_timer(0.1, self._publish_continuous)
        self._openai_client = None

        if self.command_backend in ('llm', 'hybrid'):
            api_key = self.get_parameter('openai_api_key').value or os.getenv('OPENAI_API_KEY', '')
            if not api_key:
                self.get_logger().error(
                    'LLM command backend selected but no OpenAI API key was found.'
                )
                raise SystemExit(1)
            self._openai_client = OpenAI(api_key=api_key)

        self._tool_registry = {
            'goto': self._tool_goto,
            'pickUpObject': self._tool_pick_up_object,
            'drive': self._tool_drive,
            'stop': self._tool_stop,
        }

        self.get_logger().info(
            f'Command node ready (backend={self.command_backend}) publishing velocity to {topic}'
        )

    def _publish_continuous(self):
        if self._continuous_twist is not None:
            self.publisher.publish(self._continuous_twist)

    def _on_speech(self, msg: String):
        text = msg.data.lower().strip()
        self.get_logger().info(f'Received speech: "{text}"')

        if self.command_backend == 'keyword':
            self._handle_keyword_command(text)
            return

        if self.command_backend == 'llm':
            self._handle_llm_command(text)
            return

        # Hybrid mode: try LLM first, then fallback to keyword.
        if not self._handle_llm_command(text):
            self._handle_keyword_command(text)

    def _handle_keyword_command(self, text: str) -> bool:
        twist = Twist()
        matched_keyword = None

        for keyword, (linear, angular) in COMMANDS.items():
            if keyword in text:
                twist.linear.x = linear
                twist.angular.z = angular
                matched_keyword = keyword
                self.get_logger().info(
                    f'Command: {keyword} -> linear={linear}, angular={angular}'
                )
                break

        if matched_keyword is None:
            self.get_logger().warn(f'Unknown command: "{text}"')
            return False

        if matched_keyword == 'go':
            self._continuous_twist = twist
        elif matched_keyword == 'stop':
            self._continuous_twist = None
            self.publisher.publish(twist)
        else:
            self._continuous_twist = None
            self.publisher.publish(twist)
        return True

    def _handle_llm_command(self, text: str) -> bool:
        if self._openai_client is None:
            self.get_logger().error('LLM backend requested but OpenAI client is not configured.')
            return False

        tools = [
            {
                'type': 'function',
                'function': {
                    'name': 'goto',
                    'description': 'Move the robot to a named location.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'location': {'type': 'string'}
                        },
                        'required': ['location']
                    }
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'pickUpObject',
                    'description': 'Pick up a named object.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'object_name': {'type': 'string'}
                        },
                        'required': []
                    }
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'drive',
                    'description': 'Drive turtlesim in a direction.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'direction': {
                                'type': 'string',
                                'enum': ['forward', 'backward', 'left', 'right', 'stop']
                            },
                            'speed': {'type': 'number'}
                        },
                        'required': ['direction']
                    }
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'stop',
                    'description': 'Stop all robot motion immediately.',
                    'parameters': {
                        'type': 'object',
                        'properties': {}
                    }
                },
            },
        ]

        system_prompt = (
            'You are a robot command planner. '
            'Use function tools for actionable robot requests. '
            'Keep responses short and practical.'
        )

        try:
            completion = self._openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': text},
                ],
                tools=tools,
                tool_choice='auto',
            )
        except Exception as exc:
            self.get_logger().error(f'OpenAI LLM request failed: {exc}')
            return False

        message = completion.choices[0].message
        tool_calls = message.tool_calls or []

        if not tool_calls and message.content:
            response = message.content.strip()
            self.get_logger().info(f'LLM response: {response}')
            self._speak(response)
            return True

        if not tool_calls:
            return False

        results = []
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_args = tool_call.function.arguments or '{}'
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}

            tool_fn = self._tool_registry.get(name)
            if tool_fn is None:
                results.append(f'Unsupported tool: {name}')
                continue

            try:
                result = tool_fn(**args)
            except TypeError:
                result = f'Invalid arguments for {name}: {args}'
            except Exception as exc:
                result = f'{name} failed: {exc}'
            results.append(result)

        response = ' '.join(results)
        self.get_logger().info(f'LLM tool result: {response}')
        self._speak(response)
        return True

    def _speak(self, text: str):
        if not text or not self.speak_responses or self._openai_client is None:
            return

        try:
            audio_response = self._openai_client.audio.speech.create(
                model=self.tts_model,
                voice=self.tts_voice,
                input=text,
                response_format='wav',
            )
            wav_bytes = audio_response.read()
        except Exception as exc:
            self.get_logger().warn(f'OpenAI TTS failed: {exc}')
            return

        try:
            with wave.open(io.BytesIO(wav_bytes), 'rb') as wav_file:
                channels = wav_file.getnchannels()
                sample_rate = wav_file.getframerate()
                audio_data = wav_file.readframes(wav_file.getnframes())

            pcm = np.frombuffer(audio_data, dtype=np.int16)
            if channels > 1:
                pcm = pcm.reshape(-1, channels)
            sd.play(pcm, sample_rate)
            sd.wait()
        except Exception as exc:
            self.get_logger().warn(f'Unable to play TTS audio: {exc}')

    def _tool_drive(self, direction: str, speed: float = 2.0) -> str:
        direction = direction.lower().strip()
        speed = abs(float(speed))

        mapping = {
            'forward': (speed, 0.0),
            'backward': (-speed, 0.0),
            'left': (0.0, speed),
            'right': (0.0, -speed),
            'stop': (0.0, 0.0),
        }
        if direction not in mapping:
            return f'Unknown direction: {direction}'

        linear, angular = mapping[direction]
        twist = Twist()
        twist.linear.x = linear
        twist.angular.z = angular

        if direction == 'forward':
            self._continuous_twist = twist
        elif direction == 'stop':
            self._continuous_twist = None
            self.publisher.publish(twist)
        else:
            self._continuous_twist = None
            self.publisher.publish(twist)

        return f'Driving {direction}.'

    def _tool_stop(self) -> str:
        twist = Twist()
        self._continuous_twist = None
        self.publisher.publish(twist)
        return 'Stopped.'

    def _tool_goto(self, location: str) -> str:
        location_name = location.strip()
        self._continuous_twist = None
        self.get_logger().info(f'goto() called with location="{location_name}"')
        return f'Going to {location_name}. '

    def _tool_pick_up_object(self, object_name: str = 'object') -> str:
        name = object_name.strip() if object_name else 'object'
        self._continuous_twist = None
        self.get_logger().info(f'pickUpObject() called with object_name="{name}"')
        return f'Picking up {name}.'


def main(args=None):
    rclpy.init(args=args)
    node = CommandNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
