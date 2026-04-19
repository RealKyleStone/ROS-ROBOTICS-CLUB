import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from geometry_msgs.msg import Twist

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
        topic = self.get_parameter('cmd_vel_topic').value

        self.publisher = self.create_publisher(Twist, topic, 10)
        self.subscription = self.create_subscription(
            String, '/speech_text', self._on_speech, 10
        )

        # Timer for continuous movement (e.g. "go" command)
        self._continuous_twist = None
        self._timer = self.create_timer(0.1, self._publish_continuous)

        self.get_logger().info(
            f'Command node ready — publishing velocity to {topic}'
        )

    def _publish_continuous(self):
        if self._continuous_twist is not None:
            self.publisher.publish(self._continuous_twist)

    def _on_speech(self, msg: String):
        text = msg.data.lower().strip()
        self.get_logger().info(f'Received speech: "{text}"')

        twist = Twist()
        matched = False

        for keyword, (linear, angular) in COMMANDS.items():
            if keyword in text:
                twist.linear.x = linear
                twist.angular.z = angular
                matched = True
                self.get_logger().info(
                    f'Command: {keyword} → linear={linear}, angular={angular}'
                )
                break

        if not matched:
            self.get_logger().warn(f'Unknown command: "{text}"')
            return

        # "go" starts continuous forward; "stop" cancels it; others are one-shot
        if 'go' in text:
            self._continuous_twist = twist
        elif 'stop' in text:
            self._continuous_twist = None
            self.publisher.publish(twist)
        else:
            self._continuous_twist = None
            self.publisher.publish(twist)


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
